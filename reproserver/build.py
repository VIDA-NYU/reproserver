import asyncio
import json
import kubernetes.client as k8s
import kubernetes.config
import kubernetes.watch
import logging
import os
import shutil
import subprocess
import tempfile
import time

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
            logger.info("> %s", line)
            session.add(database.BuildLogLine(
                experiment_hash=experiment_hash,
                line=line.rstrip()))
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

    def build(self, experiment_hash):
        return asyncio.get_event_loop().run_in_executor(
            None,
            self.build_sync,
            experiment_hash,
        )

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
        if not experiment:
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
    def __init__(self, *, namespace, **kwargs):
        super(K8sBuilder, self).__init__(**kwargs)
        self.namespace = namespace

    @classmethod
    def _build_in_pod(cls, namespace, experiment_hash):
        engine, DBSession = database.connect()
        object_store = get_object_store()
        builder = cls(
            namespace=namespace,
            DBSession=DBSession,
            object_store=object_store,
        )
        builder._docker_build(experiment_hash)

    def build_sync(self, experiment_hash):
        kubernetes.config.load_incluster_config()

        name = 'build-{0}'.format(experiment_hash[:55])

        # Create a Kubernetes pod to build
        # FIXME: This should be a job, but that doesn't work with multiple
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
            spec=k8s.V1PodSpec(
                restart_policy='Never',
                containers=[
                    k8s.V1Container(
                        name='docker',
                        image='docker:18.09-dind',
                        security_context=k8s.V1SecurityContext(
                            privileged=True,
                        ),
                        args=[
                            '--storage-driver=overlay2',
                            '--userns-remap=default',
                            '--insecure-registry=registry:5000',
                        ],
                    ),
                    k8s.V1Container(
                        name='builder',
                        image='reproserver_web',
                        image_pull_policy='IfNotPresent',
                        args=[
                            'python3', '-c',
                            'from reproserver.build import K8sBuilder; ' +
                            'K8sBuilder._build_in_pod{0!r}'.format((
                                self.namespace,
                                experiment_hash,
                            )),
                        ],
                        env=[
                            k8s.V1EnvVar(
                                name='SHORTIDS_SALT',
                                value_from=k8s.V1EnvVarSource(
                                    secret_key_ref=k8s.V1SecretKeySelector(
                                        name='reproserver-secret',
                                        key='salt',
                                    ),
                                ),
                            ),
                            k8s.V1EnvVar(
                                name='S3_KEY',
                                value_from=k8s.V1EnvVarSource(
                                    secret_key_ref=k8s.V1SecretKeySelector(
                                        name='reproserver-secret',
                                        key='s3_key',
                                    ),
                                ),
                            ),
                            k8s.V1EnvVar(
                                name='S3_SECRET',
                                value_from=k8s.V1EnvVarSource(
                                    secret_key_ref=k8s.V1SecretKeySelector(
                                        name='reproserver-secret',
                                        key='s3_secret',
                                    ),
                                ),
                            ),
                            k8s.V1EnvVar(
                                name='S3_URL',
                                value_from=k8s.V1EnvVarSource(
                                    config_map_key_ref=k8s.V1ConfigMapKeySelector(
                                        name='config',
                                        key='s3.url',
                                    ),
                                ),
                            ),
                            k8s.V1EnvVar(
                                name='S3_BUCKET_PREFIX',
                                value_from=k8s.V1EnvVarSource(
                                    config_map_key_ref=k8s.V1ConfigMapKeySelector(
                                        name='config',
                                        key='s3.bucket-prefix',
                                    ),
                                ),
                            ),
                            k8s.V1EnvVar(
                                name='S3_CLIENT_URL',
                                value_from=k8s.V1EnvVarSource(
                                    config_map_key_ref=k8s.V1ConfigMapKeySelector(
                                        name='config',
                                        key='s3.client-url',
                                    ),
                                ),
                            ),
                            k8s.V1EnvVar(
                                name='POSTGRES_USER',
                                value_from=k8s.V1EnvVarSource(
                                    secret_key_ref=k8s.V1SecretKeySelector(
                                        name='reproserver-secret',
                                        key='user',
                                    ),
                                ),
                            ),
                            k8s.V1EnvVar(
                                name='POSTGRES_PASSWORD',
                                value_from=k8s.V1EnvVarSource(
                                    secret_key_ref=k8s.V1SecretKeySelector(
                                        name='reproserver-secret',
                                        key='password',
                                    ),
                                ),
                            ),
                            k8s.V1EnvVar(
                                name='POSTGRES_HOST',
                                value='postgres',
                            ),
                            k8s.V1EnvVar(
                                name='POSTGRES_DB',
                                value='reproserver',
                            ),
                            k8s.V1EnvVar(
                                name='DOCKER_HOST',
                                value='tcp://127.0.0.1:2375',
                            ),
                            k8s.V1EnvVar(
                                name='REGISTRY',
                                value='registry:5000',
                            ),
                            k8s.V1EnvVar(
                                name='REPROZIP_USAGE_STATS',
                                value='off',
                            ),
                        ],
                    ),
                ],
            ),
        )

        client.create_namespaced_pod(
            namespace=self.namespace,
            body=pod,
        )

        # Watch the pod
        w = kubernetes.watch.Watch()
        f, kwargs = client.list_namespaced_pod, dict(
            namespace=self.namespace,
            label_selector='app=build,experiment={0}'.format(
                experiment_hash[:55]
            ),
        )
        started = None
        for event in w.stream(f, **kwargs):
            status = event['object'].status
            if not started and status.start_time:
                started = status.start_time
                logger.info("Build pod started: %s", started.isoformat())
            if (status.container_statuses and
                    any(c.state.terminated
                        for c in status.container_statuses)):
                w.stop()
                logger.info("Build pod succeeded")

        # Delete the pod
        client.delete_namespaced_pod(
            name=name,
            namespace=self.namespace,
        )
