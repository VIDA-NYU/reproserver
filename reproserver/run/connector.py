import asyncio
import contextlib
import hashlib
import json
import subprocess
from datetime import datetime
import logging
import os
from sqlalchemy.orm import joinedload
from tornado import gen
from tornado.httpclient import AsyncHTTPClient, HTTPClient
import urllib.parse

from .. import database


logger = logging.getLogger(__name__)


class BaseConnector(object):
    """Provides a connection to the run in the database.
    """
    RUN_CMD_LOG_INTERVAL = 1

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

    def get_input_links(self, run_info):
        """Add (internal) download URLs for each input.
        """
        raise NotImplementedError

    def download_inputs(self, run_info, directory):  # async
        """Download inputs, returns new run_info with ``local_path`` set.
        """
        raise NotImplementedError

    def get_bundle_link(self, run_info):
        """Get the (internal) URL of the bundle.
        """
        raise NotImplementedError

    def download_bundle(self, run_info, local_path):  # async
        """Download the bundle to a local file.
        """
        raise NotImplementedError

    def upload_output_file_blocking(self, run_id, name, file, *, digest=None):
        """Upload a file object to the run's output files.
        """
        raise NotImplementedError

    def upload_output_file(self, run_id, name, file, *, digest=None):  # async
        """Upload a file object to the run's output files.
        """
        raise NotImplementedError

    def log(self, run_id, msg, *args):  # async
        """Record a message to the run's log.
        """
        raise NotImplementedError

    async def log_multiple(self, run_id, lines):  # async
        """Optimized version inserting multiple logs in one go.
        """
        for line in lines:
            await self.log(run_id, '%s', line)

    async def run_cmd_and_log(self, run_id, cmd):  # async
        """Run a command, adding each line of output to the run's log.
        """
        async def log_and_wait(lines):
            await self.log_multiple(run_id, lines)
            # Don't send requests too fast
            await asyncio.sleep(self.RUN_CMD_LOG_INTERVAL)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        log_op = asyncio.Future()
        log_op.set_result(None)

        read_op = asyncio.create_task(proc.stdout.readuntil(b'\n'))

        proc_end = asyncio.create_task(proc.wait())

        lines = []

        while proc.returncode is None:
            # If we have lines to send, wait for either the next line or for
            # the current insertion to complete.
            # If we don't have anything to send, only wait for lines.
            wait_for_futures = [read_op, proc_end]
            if lines:
                wait_for_futures.append(log_op)
            await asyncio.wait(
                wait_for_futures,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # If we have read a line, add it to the list and read again
            if read_op.done():
                line = await read_op
                line = line.decode('utf-8', 'replace')
                line = line.rstrip()
                logger.info("> %s", line)
                lines.append(line)
                read_op = asyncio.create_task(proc.stdout.readuntil(b'\n'))

            # If we have completed the insertion and we have lines to send,
            # send them
            if lines and log_op.done():
                await log_op
                log_op = asyncio.create_task(log_and_wait(lines))
                lines = []

        # Send remaining lines, if any
        if lines:
            await log_op
            log_op = asyncio.create_task(self.log_multiple(run_id, lines))

        await log_op
        return await proc_end


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
                'type': port.type,
                'port_number': port.port_number,
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

    def _add_input_link(self, input_file):
        link = self.object_store.presigned_internal_url(
            'inputs',
            input_file['hash'],
        )
        return dict(input_file, link=link)

    def get_input_links(self, run_info):
        inputs = [
            self._add_input_link(input_file)
            for input_file in run_info['inputs']
        ]
        return dict(run_info, inputs=inputs)

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

    def get_bundle_link(self, run_info):
        return self.object_store.presigned_internal_url(
            'experiments',
            run_info['experiment_hash'],
        )

    def download_bundle(self, run_info, local_path):
        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.object_store.download_file(
                'experiments', run_info['experiment_hash'],
                local_path,
            ),
        )

    def upload_output_file_blocking(self, run_id, name, file, *, digest=None):
        db = self.DBSession()

        if digest is None:
            # Hash it
            hasher = hashlib.sha256()
            chunk = file.read(4096)
            while chunk:
                hasher.update(chunk)
                if len(chunk) != 4096:
                    break
                chunk = file.read(4096)
            digest = hasher.hexdigest()

            # Rewind it
            filesize = file.tell()
            file.seek(0, 0)
        else:
            file.seek(0, 2)
            filesize = file.tell()
            file.seek(0, 0)

        # Upload file to S3
        logger.info("Uploading file, size: %d bytes", filesize)
        self.object_store.upload_fileobj(
            'outputs', digest,
            file,
        )

        # Add it to database
        output_file = database.OutputFile(
            run_id=run_id,
            hash=digest,
            name=name,
            size=filesize,
        )
        db.add(output_file)
        db.commit()

    def upload_output_file(self, run_id, name, file, *, digest=None):
        # TODO: async
        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.upload_output_file_blocking(
                run_id, name, file, digest=digest,
            ),
        )

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


MAX_FILE_SIZE = 5_000_000_000  # 5 GB


def download_file(url, local_path, http_client=None):
    with contextlib.ExitStack() as http_context:
        if http_client is None:
            http_client = http_context.enter_context(
                contextlib.closing(HTTPClient(max_body_size=MAX_FILE_SIZE)),
            )
        try:
            with open(local_path, 'wb') as f_out:
                http_client.fetch(url, streaming_callback=f_out.write)
        except Exception:
            os.remove(local_path)
            raise


def file_body_producer(file):
    @gen.coroutine
    def producer(write):
        chunk = file.read(4096)
        while chunk:
            yield write(chunk)
            if len(chunk) != 4096:
                break
            chunk = file.read(4096)

    return producer


class HttpConnector(BaseConnector):
    """Connects to the API endpoint.
    """
    RUN_CMD_LOG_INTERVAL = 3

    def __init__(self, api_endpoint):
        self.api_endpoint = api_endpoint
        self.loop = asyncio.get_event_loop()
        self.http_client = AsyncHTTPClient()

    async def init_run_get_info(self, run_id):
        response = await self.http_client.fetch(
            '{0}/runners/run/{1}/init'.format(
                self.api_endpoint,
                run_id
            ),
            method='POST',
            body=b'{}',
            headers={'Content-Type': 'application/json; charset=utf-8'},
        )
        return json.loads(response.body.decode('utf-8'))

    async def run_started(self, run_id):
        await self.http_client.fetch(
            '{0}/runners/run/{1}/start'.format(
                self.api_endpoint,
                run_id
            ),
            method='POST',
            body=b'{}',
            headers={'Content-Type': 'application/json; charset=utf-8'},
        )

    async def run_done(self, run_id):
        await self.http_client.fetch(
            '{0}/runners/run/{1}/done'.format(
                self.api_endpoint,
                run_id
            ),
            method='POST',
            body=b'{}',
            headers={'Content-Type': 'application/json; charset=utf-8'},
        )

    async def run_failed(self, run_id, error):
        await self.http_client.fetch(
            '{0}/runners/run/{1}/failed'.format(
                self.api_endpoint,
                run_id
            ),
            method='POST',
            body=json.dumps({'error': error}).encode('utf-8'),
            headers={'Content-Type': 'application/json; charset=utf-8'}
        )

    def get_input_links(self, run_info):
        # The input links are already set by init_run_get_info()
        return run_info

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
        with contextlib.closing(
            HTTPClient(max_body_size=MAX_FILE_SIZE),
        ) as http_client:
            for input_file in run_info['inputs']:
                local_path = os.path.join(
                    directory,
                    'input_%s' % input_file['hash'],
                )
                logger.info(
                    "Downloading input file: %s, %s, %d bytes",
                    input_file['name'], input_file['hash'], input_file['size'],
                )

                download_file(
                    input_file['link'],
                    local_path,
                    http_client,
                )
                input_file = dict(input_file, local_path=local_path)
                inputs.append(input_file)
            return dict(run_info, inputs=inputs)

    def get_bundle_link(self, run_info):
        return run_info['experiment_url']

    def download_bundle(self, run_info, local_path):
        return asyncio.get_event_loop().run_in_executor(
            None,
            lambda: download_file(
                run_info['experiment_url'],
                local_path,
            ),
        )

    def upload_output_file_blocking(
        self, run_id, name, file,
        *, digest=None, http_client=None,
    ):
        with contextlib.ExitStack() as http_context:
            if http_client is None:
                http_client = http_context.enter_context(
                    contextlib.closing(HTTPClient()),
                )
            http_client.fetch(
                '{0}/runners/run/{1}/output/{2}'.format(
                    self.api_endpoint,
                    run_id,
                    urllib.parse.quote_plus(name),
                ),
                method='PUT',
                body_producer=file_body_producer(file)
            )

    async def upload_output_file(
        self, run_id, name, file, *, digest=None, http_client=None,
    ):
        if http_client is None:
            http_client = AsyncHTTPClient()
        await http_client.fetch(
            '{0}/runners/run/{1}/output/{2}'.format(
                self.api_endpoint,
                run_id,
                urllib.parse.quote_plus(name),
            ),
            method='PUT',
            body_producer=file_body_producer(file)
        )

    def log(self, run_id, msg, *args):
        line = msg % args
        return self.log_multiple(run_id, [line])

    async def log_multiple(self, run_id, lines):
        now = datetime.utcnow().isoformat()
        await self.http_client.fetch(
            '{0}/runners/run/{1}/log'.format(
                self.api_endpoint,
                run_id
            ),
            method='POST',
            body=json.dumps({
                'lines': [
                    {
                        'msg': line,
                        'time': now,
                    }
                    for line in lines
                ],
            }),
            headers={'Content-Type': 'application/json; charset=utf-8'},
        )
