import asyncio
import logging
import os
import random
from reprounzip_docker import select_image
import shutil
import subprocess
import tempfile
import textwrap

from .base import PROM_RUNS, BaseRunner
from ..utils import subprocess_call_async, subprocess_check_call_async, \
    shell_escape, prom_incremented


logger = logging.getLogger(__name__)


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

        await self.connector.run_progress(
            run_info['id'],
            40, "Setting up container",
        )

        # Make build directory
        directory = tempfile.mkdtemp('rpz-run')

        # Select base image from metadata
        image_name = select_image(run_info['rpz_meta']['meta'])[1]

        try:
            # Use a random directory in the container for our operations
            # This avoids conflicts
            working_dir = '/.rpz.%d' % random.randint(0, 1000000)

            # Create container
            container = 'run_%s' % run_info['id']
            logger.info(
                "Creating container %s with image %s",
                container, image_name,
            )
            cmdline = [
                'docker', 'create', '--name', container,
            ]
            for port in run_info['ports']:
                cmdline.extend([
                    '-p', '{0}:{1}:{1}'.format(bind_host, port['port_number']),
                ])
            cmdline.extend([
                '--', image_name,
                f'{working_dir}/busybox', 'sleep', '86400',
            ])
            await subprocess_check_call_async(cmdline)

            # Copy tools into container
            logger.info("Copying tools into container")
            await subprocess_check_call_async([
                'docker', 'cp', '--',
                '/opt/rpz-tools-x86_64',
                '%s:%s' % (container, working_dir)
            ])

            # Start the container (does nothing, but now we may exec)
            logger.info("Starting container")
            await subprocess_check_call_async([
                'docker', 'start', '--', container,
            ])

            # Download RPZ into container
            logger.info("Downloading RPZ into container")
            await subprocess_check_call_async(['sh', '-c', (
                'curl -fsSL '
                + shell_escape(run_info["experiment_url"])
                + ' | '
                + f'docker exec -i {container} {working_dir}/busybox sh -c'
                + f' "cat > {working_dir}/exp.rpz"'
            )])

            # Download inputs into container
            logger.info("Downloading inputs into container")
            for i, input_file in enumerate(run_info['inputs']):
                await subprocess_check_call_async(['sh', '-c', (
                    'curl -fsSL '
                    + shell_escape(input_file["link"])
                    + ' | '
                    + f'docker exec -i {container} {working_dir}/busybox sh -c'
                    + f' "cat > {working_dir}/input_{i}"'
                )])

            # Run script to move files into position
            logger.info("Moving files into position")
            script = [textwrap.dedent(
                f'''\
                set -eu

                # Extract RPZ
                cd /
                {working_dir}/rpztar {working_dir}/exp.rpz
                rm {working_dir}/exp.rpz

                # Move inputs into position
                '''
            )]
            for i, input_file in enumerate(run_info['inputs']):
                script.append(
                    'mv'
                    + ' ' + f'{working_dir}/input_{i}'
                    + ' ' + shell_escape(input_file["path"])
                    + '\n'
                )
            cmdline = [
                'docker', 'exec', '--', container,
                f'{working_dir}/busybox', 'sh', '-c', ''.join(script),
            ]
            await subprocess_check_call_async(cmdline)

            # Prepare script to run actual experiment
            script = ['set -eu\n']
            for k, cmd in sorted(run_info['parameters'].items()):
                if k.startswith('cmdline_'):
                    i = int(k[8:], 10)
                    run = run_info['rpz_meta']['runs'][i]
                    # Apply the environment
                    cmd = f'{working_dir}/busybox env -i ' + ' '.join(
                        f'{k}={shell_escape(v)}'
                        for k, v in run['environ'].items()
                    ) + ' ' + cmd
                    # Apply uid/gid
                    uid, gid = run['uid'], run['gid']
                    cmd = (
                        f'{working_dir}/rpzsudo "#{uid}" "#{gid}"'
                        + f' {working_dir}/busybox sh -c ' + shell_escape(cmd)
                    )
                    # Change to the working directory
                    wd = run['workingdir']
                    cmd = f'cd {shell_escape(wd)} && {cmd}'

                    script.append(cmd + '\n')

            # Update status in database
            await asyncio.gather(
                self.connector.run_started(run_info['id']),
                self.connector.run_progress(
                    run_info['id'],
                    80, "Container is running",
                ),
            )

            # Run command and wait until completion
            cmdline = [
                'docker', 'exec', '--', container,
                f'{working_dir}/busybox', 'sh', '-c', ''.join(script),
            ]
            logger.info("Running experiment")
            try:
                ret = await self.connector.run_cmd_and_log(
                    run_info['id'],
                    cmdline,
                )
            except IOError:
                raise ValueError("Got IOError running experiment")
            if ret != 0:
                raise ValueError("Error: Docker returned %d" % ret)
            logger.info("Container done")

            # Get output files
            logs = await self._upload_output_files(
                run_info, container, directory,
            )
            if logs:
                await self.connector.log_multiple(run_info['id'], logs)
            await self.connector.run_done(run_info['id'])
        finally:
            # Remove container if created
            if container is not None:
                subprocess.call(['docker', 'rm', '-f', '--', container])
            # Remove temp directory
            shutil.rmtree(directory)

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
