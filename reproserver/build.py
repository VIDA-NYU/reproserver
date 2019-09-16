import asyncio
import json
import kubernetes.client as k8s
from kubernetes.client.rest import ApiException as K8sException
import kubernetes.config
import kubernetes.watch
import logging
import os
import shutil
import subprocess
import tempfile
import time
import yaml

from reproserver import database
from reproserver.objectstore import get_object_store
from reproserver.utils import shell_escape


logger = logging.getLogger(__name__)


# IP as understood by Docker daemon, not this container
DOCKER_REGISTRY = os.environ.get('REGISTRY', 'localhost:5000')


def run_cmd_and_log(session, experiment_hash, cmd):
    session.add(database.BuildLogLine(
        experiment_hash=experiment_hash,
        line=' '.join(cmd)))
    session.commit()
    proc = subprocess.Popen(cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    proc.stdin.close()
    try:
        for line in iter(proc.stdout.readline, b''):
            line = line.decode('utf-8', 'replace')
            line = line.rstrip()
            logger.info("> %s", line)
            session.add(database.BuildLogLine(
                experiment_hash=experiment_hash,
                line=line,
            ))
            session.commit()
        ret = proc.wait()
        if ret != 0:
            return "Process returned %d" % proc.returncode
    except IOError:
        return "Got IOError"


class Builder(object):
    def __init__(self, DBSession, object_store):
        self.DBSession = DBSession
        self.object_store = object_store

        self._builds = {}

    def build(self, experiment_hash):
        # We keep a cache of future
        # If we already have a task building this, return its future
        if experiment_hash in self._builds:
            return self._builds[experiment_hash]

        # At the end of the build task, remove from dict and log
        def callback(future):
            del self._builds[experiment_hash]
            try:
                future.result()
            except Exception:
                logger.exception("Exception in build task")

        # Run the task in threadpool
        future = asyncio.get_event_loop().run_in_executor(
            None,
            self.build_sync,
            experiment_hash,
        )
        self._builds[experiment_hash] = future
        future.add_done_callback(callback)
        return future

    def build_sync(self, experiment_hash):
        raise NotImplementedError


class DockerBuilder(Builder):
    def build_sync(self, experiment_hash):
        self._docker_build(experiment_hash)

    def _docker_build(self, experiment_hash):
        """Process a build task.

        Lookup the experiment in the database, and the file on S3. Then, do the
        build, upload the log, and fill in the parameters in the database.
        """
        logger.info("Build request received: %r", experiment_hash)

        # Look up the experiment in the database
        db = self.DBSession()
        experiment = db.query(database.Experiment).get(experiment_hash)
        if experiment is None:
            raise KeyError("Unknown experiment %r", experiment_hash)

        # Update status in database
        if experiment.status != database.Status.QUEUED:
            logger.warning("Building experiment which has status %r",
                           experiment.status)
        experiment.status = database.Status.BUILDING
        experiment.docker_image = None
        experiment.parameters[:] = []
        experiment.paths[:] = []
        experiment.log[:] = []
        db.commit()
        logger.info("Set status to BUILDING")

        # Make build directory
        directory = tempfile.mkdtemp('build_%s' % experiment.hash)

        try:
            # Get experiment file
            logger.info("Downloading file...")
            local_path = os.path.join(directory, 'experiment.rpz')
            build_dir = os.path.join(directory, 'build_dir')
            self.object_store.download_file(
                'experiments', experiment.hash,
                local_path,
            )
            logger.info("Got file, %d bytes", os.stat(local_path).st_size)

            # Get metadata
            info_proc = subprocess.Popen(
                ['reprounzip', 'info', '--json', local_path],
                stdout=subprocess.PIPE,
            )
            info_stdout, _ = info_proc.communicate()
            if info_proc.wait() != 0:
                raise ValueError("Error getting info from package")
            info = json.loads(info_stdout.decode('utf-8'))
            logger.info("Got metadata, %d runs", len(info['runs']))

            # Remove previous build log
            experiment.log[:] = []
            db.commit()

            # Wait for Docker to come up
            for _ in range(6):
                proc = subprocess.Popen(
                    ['docker', 'version'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                proc.communicate()
                if proc.returncode == 0:
                    break
                time.sleep(5)
            else:
                raise RuntimeError("Couldn't connect to Docker")

            # Build the experiment
            image_name = 'rpuz_exp_%s' % experiment.hash
            fq_image_name = '%s/%s' % (DOCKER_REGISTRY, image_name)
            logger.info("Building image %s...", fq_image_name)
            err = run_cmd_and_log(
                db,
                experiment.hash,
                [
                    'reprounzip', '-v', 'docker', 'setup',
                    '--image-name', fq_image_name,
                    local_path, build_dir,
                ],
            )
            if err is not None:
                raise ValueError(err)

            db.add(database.BuildLogLine(
                experiment_hash=experiment.hash,
                line="Build successful",
            ))
            experiment.docker_image = image_name
            db.commit()
            logger.info("Build over, pushing image")

            # Push image to Docker repository
            subprocess.check_call(['docker', 'push', fq_image_name])
            logger.info("Push complete, finishing up")

            # Add parameters
            # Command-line of each run
            for i, run in enumerate(info['runs']):
                cmdline = ' '.join(shell_escape(a) for a in run['argv'])
                db.add(database.Parameter(
                    experiment_hash=experiment.hash,
                    name="cmdline_%d" % i, optional=False, default=cmdline,
                    description="Command-line for step %s" % run['id']),
                )
            # Input/output files
            for name, iofile in info.get('inputs_outputs', ()).items():
                path = iofile['path']

                # It's an input if it's read before it is written
                if iofile['read_runs'] and iofile['write_runs']:
                    first_write = min(iofile['write_runs'])
                    first_read = min(iofile['read_runs'])
                    is_input = first_read <= first_write
                else:
                    is_input = bool(iofile['read_runs'])

                # It's an output if it's ever written
                is_output = bool(iofile['write_runs'])

                db.add(database.Path(
                    experiment_hash=experiment.hash,
                    is_input=is_input,
                    is_output=is_output,
                    name=name,
                    path=path),
                )

            # Set status
            experiment.status = database.Status.BUILT
            db.commit()
            logger.info("Done!")
        except Exception as e:
            logger.exception("Error processing build!")
            logger.warning("Got error: %s", str(e))
            experiment.status = database.Status.ERROR
            db.add(database.BuildLogLine(
                experiment_hash=experiment.hash,
                line=str(e)),
            )
            db.commit()
        finally:
            # Remove build directory
            shutil.rmtree(directory)


class K8sBuilder(DockerBuilder):
    @classmethod
    def _build_in_pod(cls, experiment_hash):
        logging.root.handlers.clear()
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s: %(message)s")

        DBSession = database.connect()
        object_store = get_object_store()
        builder = cls(
            DBSession=DBSession,
            object_store=object_store,
        )
        builder._docker_build(experiment_hash)

    def build_sync(self, experiment_hash):
        kubernetes.config.load_incluster_config()

        name = 'build-{0}'.format(experiment_hash[:55])

        # Load configuration from configmap volume
        with open('/etc/k8s-config/builder.pod_spec') as fp:
            pod_spec = yaml.safe_load(fp)
        with open('/etc/k8s-config/builder.namespace') as fp:
            namespace = fp.read().strip()

        # Make required changes
        for container in pod_spec['containers']:
            if container['name'] == 'builder':
                container['args'] = [
                    'python3', '-c',
                    'from reproserver.build import K8sBuilder; ' +
                    'K8sBuilder._build_in_pod{0!r}'.format((
                        experiment_hash,
                    )),
                ]

        # Create a Kubernetes pod to build
        # FIXME: This should be a Job, but that doesn't work with multiple
        # containers: https://github.com/kubernetes/kubernetes/issues/25908
        client = k8s.CoreV1Api()
        pod = k8s.V1Pod(
            api_version='v1',
            kind='Pod',
            metadata=k8s.V1ObjectMeta(
                name=name,
                labels={
                    'app': 'build',
                    'experiment': experiment_hash[:55],
                },
            ),
            spec=pod_spec,
        )
        try:
            client.create_namespaced_pod(
                namespace=namespace,
                body=pod,
            )
        except K8sException as e:
            if e.status == 409:  # Conflict: pod already exists
                logger.info("Pod already exists")
            else:
                raise
        else:
            logger.info("Pod created")

        # Watch the pod
        w = kubernetes.watch.Watch()
        f, kwargs = client.list_namespaced_pod, dict(
            namespace=namespace,
            label_selector='app=build,experiment={0}'.format(
                experiment_hash[:55]
            ),
        )
        started = None
        success = False
        for event in w.stream(f, **kwargs):
            status = event['object'].status
            if not started and status.start_time:
                started = status.start_time
                logger.info("Build pod started: %s", started.isoformat())
            if (status.container_statuses and
                    any(c.state.terminated
                        for c in status.container_statuses)):
                w.stop()

                # Check the status of all containers
                for container in status.container_statuses:
                    terminated = container.state.terminated
                    if terminated:
                        exit_code = terminated.exit_code
                        if container.name == 'builder' and exit_code == 0:
                            logger.info("Build pod succeeded")
                            success = True
                        elif exit_code is not None:
                            # Log any container that exited, including builder
                            # if status is not zero
                            log = client.read_namespaced_pod_log(
                                name,
                                namespace,
                                container=container.name,
                                tail_lines=300,
                            )
                            log = '\n'.join(
                                '    %s' % l
                                for l in log.splitlines()
                            )
                            logger.info(
                                "Container %s exited with %d\n%s",
                                container.name,
                                exit_code,
                                log,
                            )

        if not success:
            logger.warning("Building experiment %r failed", experiment_hash)
            db = self.DBSession()
            experiment = db.query(database.Experiment).get(experiment_hash)
            if experiment is None:
                logger.warning("Experiment not in database, can't set status")
            else:
                experiment.status = database.Status.ERROR
                db.commit()

        # Delete the pod
        time.sleep(60)
        try:
            client.delete_namespaced_pod(
                name=name,
                namespace=namespace,
            )
        except K8sException as e:
            if e.status == 404:  # Not found: deleted concurrently
                pass
            else:
                raise
