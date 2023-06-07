import asyncio
import logging
import os
import shutil
import subprocess
import tempfile

from .base import PROM_RUNS, BaseRunner
from ..utils import subprocess_call_async, subprocess_check_call_async, \
    shell_escape, prom_incremented


logger = logging.getLogger(__name__)


# IP as understood by Docker daemon, not this container
DOCKER_REGISTRY = os.environ.get('REGISTRY', 'localhost:5000')


class DockerRunner(BaseRunner):
    """Docker runner implementation.

    This talks to Docker directly to pull, build, and run an image. It is used
    when running with docker-compose; on Kubernetes, the subclass K8sRunner
    will be used to schedule a pod that will run _docker_run().
    """
    async def run_inner(self, run_info):
        # Straight-up Docker, e.g. we're using docker-compose
        # Run and build right here
        with prom_incremented(PROM_RUNS):
            await self._docker_run(
                run_info,
                '0.0.0.0',  # Accept connections to proxy from everywhere
            )

    async def get_image(self, run_info):
        experiment_hash = run_info['experiment_hash']

        push_process = None
        fq_image_name = '%s/%s' % (
            DOCKER_REGISTRY,
            'rpuz_exp_%s' % experiment_hash,
        )
        logger.info("Image name: %s", fq_image_name)
        pull_proc = await asyncio.create_subprocess_exec(
            'docker', 'pull', fq_image_name,
        )
        ret = await pull_proc.wait()
        if ret == 0:
            logger.info("Pulled image from cache")
        else:
            logger.info("Couldn't get image from cache, building")
            with tempfile.TemporaryDirectory() as directory:
                # Get experiment file
                logger.info("Downloading file...")
                local_path = os.path.join(directory, 'experiment.rpz')
                build_dir = os.path.join(directory, 'build_dir')
                await self.connector.download_bundle(
                    run_info,
                    local_path,
                )
                logger.info("Got file, %d bytes", os.stat(local_path).st_size)

                # Build image
                ret = await subprocess_call_async([
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
            push_process = await asyncio.create_subprocess_exec(
                'docker', 'push', fq_image_name,
            )

        return fq_image_name, push_process

    async def _docker_run(self, run_info, bind_host):
        """Pull or build an image, then run it.

        Lookup a run in the database, build the image, get the input files from
        S3, then do the run from the Docker image, upload the log and the
        output files.

        This is run either in the main process, when using DockerRunner (e.g.
        when using docker-compose) or it is run in another pod (when using
        K8sRunner).
        """
        container = None

        extra_config = run_info['extra_config']
        if extra_config is not None:
            if extra_config.get('required'):
                raise ValueError("Unsupported required extra config: %s" % (
                    ", ".join(extra_config['required']),
                ))

        # Make build directory
        directory = tempfile.mkdtemp('build_%s' % run_info['experiment_hash'])

        try:
            # Download input files
            input_download_future = asyncio.ensure_future(
                self.connector.download_inputs(run_info, directory),
            )

            # Get or build the Docker image
            get_image_future = asyncio.ensure_future(
                self.get_image(run_info),
            )

            # Wait for both tasks to finish
            run_info, (fq_image_name, push_process) = await asyncio.gather(
                input_download_future,
                get_image_future,
            )

            # Create container
            container = 'run_%s' % run_info['id']
            logger.info(
                "Creating container %s with image %s",
                container, fq_image_name,
            )
            # Turn parameters into a command-line
            cmdline = [
                'docker', 'create', '-i', '--name', container,
            ]
            for port in run_info['ports']:
                cmdline.extend([
                    '-p', '{0}:{1}:{1}'.format(bind_host, port['port_number']),
                ])
            cmdline.extend([
                '--', fq_image_name,
            ])
            for k, v in sorted(run_info['parameters'].items()):
                if k.startswith('cmdline_'):
                    i = str(int(k[8:], 10))
                    cmdline.extend(['cmd', v, 'run', i])
            logger.info('$ %s', ' '.join(shell_escape(a) for a in cmdline))

            # Create container
            await subprocess_check_call_async(cmdline)

            # Put input files in container
            await self._load_input_files(run_info, container)

            # Update status in database
            logger.info("Starting container")
            await self.connector.run_started(run_info['id'])

            # Start container and wait until completion
            try:
                ret = await self.connector.run_cmd_and_log(
                    run_info['id'],
                    ['docker', 'start', '-ai', '--', container],
                )
            except IOError:
                raise ValueError("Got IOError running experiment")
            if ret != 0:
                raise ValueError("Error: Docker returned %d" % ret)
            logger.info("Container done")
            await self.connector.run_done(run_info['id'])

            # Get output files
            logs = await self._upload_output_files(
                run_info, container, directory,
            )
            await self.connector.log_multiple(run_info['id'], logs)
        finally:
            # Remove container if created
            if container is not None:
                subprocess.call(['docker', 'rm', '-f', '--', container])
            # Remove build directory
            shutil.rmtree(directory)

        # Wait for push process to end
        if push_process:
            if push_process.returncode is None:
                logger.info("Waiting for docker push to finish...")
            ret = await push_process.wait()
            logger.info("docker push returned %d", ret)

    async def _load_input_files(self, run_info, container):
        for input_file in run_info['inputs']:
            logger.info("Copying file to container")
            await subprocess_check_call_async([
                'docker', 'cp', '--',
                input_file['local_path'],
                '%s:%s' % (container, input_file['path']),
            ])

            os.remove(input_file['local_path'])

    async def _upload_output_files(self, run_info, container, directory):
        logs = []

        for path in run_info['outputs']:
            local_path = os.path.join(
                directory,
                'output_%s' % path['name'],
            )

            # Copy file out of container
            logger.info("Getting output file %s", path['name'])
            ret = await subprocess_call_async([
                'docker', 'cp', '--',
                '%s:%s' % (container, path['path']),
                local_path,
            ])
            if ret != 0:
                logger.warning("Couldn't get output %s", path['name'])
                logs.append("Couldn't get output %s" % path['name'])
                continue

            # Upload file
            with open(local_path, 'rb') as file:
                await self.connector.upload_output_file(
                    run_info['id'],
                    path['name'],
                    file,
                )

            # Remove local file
            os.remove(local_path)

        return logs


Runner = DockerRunner
