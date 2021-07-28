from datetime import datetime
from hashlib import sha256
import logging
import os
import shutil
from sqlalchemy.orm import joinedload
import subprocess
import tempfile

from .. import database
from ..utils import shell_escape
from .base import BaseRunner, run_cmd_and_log


logger = logging.getLogger(__name__)


# IP as understood by Docker daemon, not this container
DOCKER_REGISTRY = os.environ.get('REGISTRY', 'localhost:5000')


class DockerRunner(BaseRunner):
    """Docker runner implementation.

    This talks to Docker directly to pull, build, and run an image. It is used
    when running with docker-compose; on Kubernetes, the subclass K8sRunner
    will be used to schedule a pod that will run _docker_run().
    """
    def run_sync(self, run_id):
        # Straight-up Docker, e.g. we're using docker-compose
        # Run and build right here
        self._docker_run(
            run_id,
            '0.0.0.0',  # Accept connections to proxy from everywhere
        )

    def _docker_run(self, run_id, bind_host):
        """Pull or build an image, then run it.

        Lookup a run in the database, build the image, get the input files from
        S3, then do the run from the Docker image, upload the log and the
        output files.

        This is run either in the main process, when using DockerRunner (e.g.
        when using docker-compose) or it is run in another pod (when using
        K8sRunner).
        """
        logger.info("Run request received: %r", run_id)

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

        # Get or build the Docker image
        push_process = None
        fq_image_name = '%s/%s' % (
            DOCKER_REGISTRY,
            'rpuz_exp_%s' % run.experiment.hash,
        )
        logger.info("Image name: %s", fq_image_name)
        ret = subprocess.call(['docker', 'pull', fq_image_name])
        if ret == 0:
            logger.info("Pulled image from cache")
        else:
            logger.info("Couldn't get image from cache, building")
            with tempfile.TemporaryDirectory() as directory:
                # Get experiment file
                logger.info("Downloading file...")
                local_path = os.path.join(directory, 'experiment.rpz')
                build_dir = os.path.join(directory, 'build_dir')
                self.object_store.download_file(
                    'experiments', run.experiment.hash,
                    local_path,
                )
                logger.info("Got file, %d bytes", os.stat(local_path).st_size)

                # Build image
                ret = subprocess.call([
                    'reprounzip', '-v', 'docker', 'setup',
                    # `RUN --mount` doesn't work with userns-remap
                    '--dont-use-buildkit',
                    '--image-name', fq_image_name,
                    local_path, build_dir,
                ])
                if ret != 0:
                    raise ValueError("Error: Docker returned %d" % ret)
            logger.info("Build over, pushing image")

            # Push image to Docker repository in the background
            push_process = subprocess.Popen(['docker', 'push', fq_image_name])

        # Remove previous info
        run.log[:] = []
        run.output_files[:] = []

        # Make build directory
        directory = tempfile.mkdtemp('build_%s' % run.experiment_hash)

        container = None

        try:
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
                    logger.info("Param: %s=%r", param.name, param.value)
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
                inputs.append((input_file,
                               paths[input_file.name]))
            logger.info(
                "Using %d input files: %s",
                len(inputs),
                ", ".join(f.name for f, p in inputs),
            )

            # Create container
            container = 'run_%s' % run_id
            logger.info(
                "Creating container %s with image %s",
                container, fq_image_name,
            )
            # Turn parameters into a command-line
            cmdline = [
                'docker', 'create', '-i', '--name', container,
            ]
            for port in run.ports:
                cmdline.extend([
                    '-p', '{0}:{1}:{1}'.format(bind_host, port.port_number),
                ])
            cmdline.extend([
                '--', fq_image_name,
            ])
            for k, v in sorted(params.items()):
                if k.startswith('cmdline_'):
                    i = str(int(k[8:], 10))
                    cmdline.extend(['cmd', v, 'run', i])
            logger.info('$ %s', ' '.join(shell_escape(a) for a in cmdline))
            subprocess.check_call(cmdline)

            for input_file, path in inputs:
                local_path = os.path.join(
                    directory,
                    'input_%s' % input_file.hash,
                )

                # Download file from S3
                logger.info(
                    "Downloading input file: %s, %s, %d bytes",
                    input_file.name, input_file.hash, input_file.size,
                )
                self.object_store.download_file(
                    'inputs', input_file.hash,
                    local_path,
                )

                # Put file in container
                logger.info("Copying file to container")
                subprocess.check_call(
                    [
                        'docker', 'cp', '--',
                        local_path,
                        '%s:%s' % (container, path),
                    ],
                )

                # Remove local file
                os.remove(local_path)

            # Update status in database
            logger.info("Starting container")
            if run.started:
                logger.warning("Starting run which has already been started")
            else:
                run.started = datetime.utcnow()
                db.commit()

            # Start container using parameters
            try:
                ret = run_cmd_and_log(
                    db,
                    run.id,
                    ['docker', 'start', '-ai', '--', container],
                    to_db=lambda l, run_id=run.id: (
                        database.RunLogLine(run_id=run_id, line=l)
                    ),
                )
            except IOError:
                raise ValueError("Got IOError running experiment")
            if ret != 0:
                raise ValueError("Error: Docker returned %d" % ret)
            logger.info("Container done")
            run.done = datetime.utcnow()

            # Get output files
            for path in run.experiment.paths:
                if path.is_output:
                    local_path = os.path.join(
                        directory,
                        'output_%s' % path.name,
                    )

                    # Copy file out of container
                    logger.info("Getting output file %s", path.name)
                    ret = subprocess.call(
                        [
                            'docker', 'cp', '--',
                            '%s:%s' % (container, path.path),
                            local_path,
                        ],
                    )
                    if ret != 0:
                        logger.warning("Couldn't get output %s", path.name)
                        db.add(database.RunLogLine(
                            run_id=run.id,
                            line="Couldn't get output %s" % path.name,
                        ))
                        continue

                    with open(local_path, 'rb') as fp:
                        # Hash it
                        hasher = sha256()
                        chunk = fp.read(4096)
                        while chunk:
                            hasher.update(chunk)
                            chunk = fp.read(4096)
                        filehash = hasher.hexdigest()

                        # Rewind it
                        filesize = fp.tell()
                        fp.seek(0, 0)

                        # Upload file to S3
                        logger.info("Uploading file, size: %d bytes", filesize)
                        self.object_store.upload_fileobj(
                            'outputs', filehash,
                            fp,
                        )

                    # Add OutputFile to database
                    run.output_files.append(database.OutputFile(
                        hash=filehash,
                        name=path.name,
                        size=filesize,
                    ))

                    # Remove local file
                    os.remove(local_path)

            db.commit()
            logger.info("Done!")
        except Exception as e:
            logger.exception("Error processing run!")
            logger.warning("Got error: %s", str(e))
            run.done = datetime.utcnow()
            db.add(database.RunLogLine(run_id=run.id, line=str(e)))
            db.commit()
        finally:
            # Remove container if created
            if container is not None:
                subprocess.call(['docker', 'rm', '-f', '--', container])
            # Remove build directory
            shutil.rmtree(directory)

        # Wait for push process to end
        if push_process:
            if push_process.poll() is None:
                logger.info("Waiting for docker push to finish...")
                push_process.wait()
            logger.info("docker push returned %d", push_process.returncode)


Runner = DockerRunner
