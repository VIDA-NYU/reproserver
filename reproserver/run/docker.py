import asyncio
import logging
import os
import random
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

        # TODO: Select base image from metadata
        run_info['rpz_meta']['meta']['distribution']
        image_name = 'ubuntu:22.04'

        try:
            # Create container
            container = 'run_%s' % run_info['id']
            logger.info(
                "Creating container %s with image %s",
                container, image_name,
            )

            working_dir = '/.rpz.%d' % random.randint(0, 1000000)
            rpztar = '/.rpztar.%d' % random.randint(0, 1000000)
            script = [textwrap.dedent(
                f'''\
                set -eu

                apt-get update && apt-get install -yy curl busybox-static # TODO

                mkdir {working_dir}

                # Download RPZ
                curl -Lo {working_dir}/exp.rpz {shell_escape(run_info['experiment_url'])}

                # Download inputs
                '''
            )]
            for i, input_file in enumerate(run_info['inputs']):
                script.append(
                    'curl -Lo'
                    + ' ' + f'input_{i}'
                    + ' ' + shell_escape(input_file["link"])
                    + '\n'
                )
            script.append(textwrap.dedent(
                f'''\

                # Extract RPZ
                cd /
                {rpztar} {working_dir}/exp.rpz
                rm {working_dir}/exp.rpz
                rm {rpztar}

                # Move inputs into position
                '''
            ))
            for i, input_file in enumerate(run_info['inputs']):
                script.append(
                    'mv'
                    + ' ' + f'{working_dir}/input_{i}'
                    + ' ' + shell_escape(input_file["path"])
                    + '\n'
                )
            script.append(textwrap.dedent(
                f'''\

                # Run commands
                '''
            ))
            for k, cmd in sorted(run_info['parameters'].items()):
                if k.startswith('cmdline_'):
                    i = int(k[8:], 10)
                    run = run_info['rpz_meta']['runs'][i]
                    # Apply the environment
                    cmd = '/bin/busybox env -i ' + ' '.join(
                        f'{k}={shell_escape(v)}'
                        for k, v in run['environ'].items()
                    ) + ' ' + cmd
                    # Apply uid/gid
                    cmd = (
                        f'/rpzsudo "#{run["uid"]}" "#{run["gid"]}"'
                        + ' /bin/busybox sh -c ' + shell_escape(cmd)
                    )
                    # Change to the working directory
                    wd = run['workingdir']
                    cmd = f'cd {shell_escape(wd)} && {cmd}'

                    script.append(cmd + '\n')
            script = ''.join(script)

            cmdline = [
                'docker', 'create', '-i', '--name', container,
            ]
            for port in run_info['ports']:
                cmdline.extend([
                    '-p', '{0}:{1}:{1}'.format(bind_host, port['port_number']),
                ])
            cmdline.extend([
                '--', image_name,
            ])
            cmdline.extend(['sh', '-c', script])
            logger.info('$ %s', ' '.join(shell_escape(a) for a in cmdline))

            # Create container
            await subprocess_check_call_async(cmdline)

            # Put rpztar in container
            await subprocess_check_call_async([
                'docker', 'cp', '--',
                '/bin/rpztar-x86_64',
                '%s:%s' % (container, rpztar)
            ])

            # Put rpzsudo in container
            await subprocess_check_call_async([
                'docker', 'cp', '--',
                '/bin/rpzsudo-x86_64',
                '%s:/rpzsudo' % (container,),
            ])

            # Update status in database
            logger.info("Starting container")
            await asyncio.gather(
                self.connector.run_started(run_info['id']),
                self.connector.run_progress(
                    run_info['id'],
                    80, "Container is running",
                ),
            )

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
