import asyncio
import hashlib
import subprocess
from datetime import datetime
import logging
import os
from sqlalchemy.orm import joinedload

from .. import database


logger = logging.getLogger(__name__)


class BaseConnector(object):
    """Provides a connection to the run in the database.
    """
    def init_run_get_info(self, run_id):  # async
        """Get information for a run, mark it as starting.
        """
        raise NotImplementedError

    def run_started(self, run_id):  # async
        """Mark run as currently running, set start time.
        """
        raise NotImplementedError

    def run_done(self, run_id):  # async
        """Mark run as completed, set end date.
        """
        raise NotImplementedError

    def run_failed(self, run_id, error):  # async
        """Mark run as failed.
        """
        raise NotImplementedError

    def download_inputs(self, run_info, directory):  # async
        """Download inputs, returns new run_info with ``local_path`` set.
        """
        raise NotImplementedError

    def download_bundle(self, experiment_hash, local_path):  # async
        raise NotImplementedError

    def upload_output_file_blocking(self, run_id, name, file):
        raise NotImplementedError

    def log(self, run_id, msg, *args):  # async
        raise NotImplementedError

    async def log_multiple(self, run_id, lines):  # async
        """Optimized version inserting multiple logs in one go.
        """
        for line in lines:
            await self.log(run_id, '%s', line)

    def run_cmd_and_log(self, run_id, cmd):  # async
        """Run a command, adding each line of output to the run's log.
        """
        raise NotImplementedError


class DirectConnector(BaseConnector):
    """Connects to the database directly.
    """
    def __init__(self, *, DBSession, object_store):
        self.DBSession = DBSession
        self.object_store = object_store
        super(DirectConnector, self).__init__()

    async def init_run_get_info(self, run_id):
        # Look up the run in the database
        db = self.DBSession()
        exp = joinedload(database.Run.experiment)
        run = (
            db.query(database.Run)
            .options(joinedload(database.Run.parameter_values),
                     joinedload(database.Run.input_files),
                     joinedload(database.Run.ports),
                     exp.joinedload(database.Experiment.parameters),
                     exp.joinedload(database.Experiment.paths))
        ).get(run_id)
        if run is None:
            raise KeyError("Unknown run %r", run_id)

        # Get list of parameters
        params = {}
        params_unset = set()
        for param in run.experiment.parameters:
            if not param.optional:
                params_unset.add(param.name)
            params[param.name] = param.default

        # Get parameter values
        for param in run.parameter_values:
            if param.name in params:
                params[param.name] = param.value
                params_unset.discard(param.name)
            else:
                raise ValueError("Got parameter value for parameter %s "
                                 "which does not exist" % param.name)

        if params_unset:
            raise ValueError(
                "Missing value for parameters: %s" %
                ", ".join(params_unset)
            )

        # Get paths
        paths = {}
        for path in run.experiment.paths:
            paths[path.name] = path.path

        # Get input files
        inputs = []
        for input_file in run.input_files:
            if input_file.name not in paths:
                raise ValueError("Got an unknown input file %s" %
                                 input_file.name)
            inputs.append({
                'name': input_file.name,
                'input_hash': input_file.hash,
                'path': paths[input_file.name],
                'size': input_file.size,
            })

        # Get output files
        outputs = []
        for path in run.experiment.paths:
            if path.is_output:
                outputs.append({
                    'name': path.name,
                    'path': path.path,
                })

        # Get ports
        ports = []
        for port in run.ports:
            ports.append({
                'type': port['type'],
                'port_number': port['port_number'],
            })

        # Remove previous info
        run.log[:] = []
        run.output_files[:] = []
        db.commit()

        return {
            'id': run_id,
            'experiment_hash': run.experiment.hash,
            'parameters': params,
            'inputs': inputs,
            'outputs': outputs,
            'ports': ports,
        }

    async def run_started(self, run_id):
        db = self.DBSession()
        run = db.query(database.Run).get(run_id)
        if run.started:
            logger.warning("Starting run which has already been started")
        else:
            run.started = datetime.utcnow()
            db.commit()

    async def run_done(self, run_id):
        db = self.DBSession()
        run = db.query(database.Run).get(run_id)
        run.done = datetime.utcnow()
        db.commit()

    async def run_failed(self, run_id, error):
        db = self.DBSession()
        run = db.query(database.Run).get(run_id)
        run.done = datetime.utcnow()
        db.add(database.RunLogLine(run_id=run.id, line=error))
        db.commit()

    def download_inputs(self, run_info, directory):
        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._download_inputs(
                run_info,
                directory,
            ),
        )

    def _download_inputs(self, run_info, directory):
        inputs = []
        for input_file in run_info['inputs']:
            local_path = os.path.join(
                directory,
                'input_%s' % input_file['hash'],
            )
            logger.info(
                "Downloading input file: %s, %s, %d bytes",
                input_file['name'], input_file['hash'], input_file['size'],
            )
            self.object_store.download_file(
                'inputs', input_file['hash'],
                local_path,
            )
            input_file = dict(input_file, local_path=local_path)
            inputs.append(input_file)
        return dict(run_info, inputs=inputs)

    def download_bundle(self, experiment_hash, local_path):
        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.object_store.download_file(
                'experiments', experiment_hash,
                local_path,
            ),
        )

    def upload_output_file_blocking(self, run_id, name, file):
        db = self.DBSession()

        # Hash it
        hasher = hashlib.sha256()
        chunk = file.read(4096)
        while chunk:
            hasher.update(chunk)
            if len(chunk) != 4096:
                break
            chunk = file.read(4096)
        filehash = hasher.hexdigest()

        # Rewind it
        filesize = file.tell()
        file.seek(0, 0)

        # Upload file to S3
        logger.info("Uploading file, size: %d bytes", filesize)
        self.object_store.upload_fileobj(
            'outputs', filehash,
            file,
        )

        # Add it to database
        output_file = database.OutputFile(
            run_id=run_id,
            hash=filehash,
            name=name,
            size=filesize,
        )
        db.add(output_file)
        db.commit()

    async def log(self, run_id, msg, *args):
        db = self.DBSession()
        line = msg % args
        db.add(database.RunLogLine(run_id=run_id, line=line))
        db.commit()

    async def log_multiple(self, run_id, lines):
        db = self.DBSession()
        for line in lines:
            db.add(database.RunLogLine(run_id=run_id, line=line))
        db.commit()

    def _run_cmd_and_log(self, run_id, cmd):
        db = self.DBSession()
        proc = subprocess.Popen(cmd,
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        proc.stdin.close()
        for line in iter(proc.stdout.readline, b''):
            line = line.decode('utf-8', 'replace')
            line = line.rstrip()
            logger.info("> %s", line)
            db.add(database.RunLogLine(run_id=run_id, line=line))
            db.commit()
        return proc.wait()

    def run_cmd_and_log(self, run_id, cmd):
        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._run_cmd_and_log(
                run_id, cmd,
            ),
        )
