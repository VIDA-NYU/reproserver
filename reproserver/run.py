import asyncio
from hashlib import sha256
import kubernetes.client as k8s
import kubernetes.config
import kubernetes.watch
import logging
import os
import shutil
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import functions
import subprocess
import tempfile

from reproserver import database
from reproserver.objectstore import get_object_store
from reproserver.utils import shell_escape


logger = logging.getLogger(__name__)


# IP as understood by Docker daemon, not this container
DOCKER_REGISTRY = os.environ.get('REGISTRY', 'localhost:5000')


def run_cmd_and_log(session, run_id, cmd):
    proc = subprocess.Popen(cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    proc.stdin.close()
    for line in iter(proc.stdout.readline, b''):
        line = line.decode('utf-8', 'replace')
        logger.info("> %s", line)
        session.add(database.RunLogLine(
            run_id=run_id,
            line=line.rstrip()))
        session.commit()
    return proc.wait()


class Runner(object):
    def __init__(self, DBSession, object_store):
        self.DBSession = DBSession
        self.object_store = object_store

    def run(self, run_id):
        return asyncio.get_event_loop().run_in_executor(
            None,
            self.run_sync,
            run_id,
        )

    def run_sync(self, run_id):
        raise NotImplementedError


class DockerRunner(Runner):
    def run_sync(self, run_id):
        self._docker_run(run_id)

    def _docker_run(self, run_id):
        """Run a built experiment.

        Lookup a run in the database, get the input files from S3, then do the
        run from the Docker image, upload the log and the output files.
        """
        logger.info("Run request received: %r", run_id)

        # Look up the run in the database
        db = self.DBSession()
        exp = joinedload(database.Run.experiment)
        run = (
            db.query(database.Run)
            .options(joinedload(database.Run.parameter_values),
                     joinedload(database.Run.input_files),
                     exp.joinedload(database.Experiment.parameters),
                     exp.joinedload(database.Experiment.paths))
        ).get(run_id)
        if not run:
            raise KeyError("Unknown run %r", run_id)

        # Update status in database
        if run.started:
            logger.warning("Starting run which has already been started")
        else:
            run.started = functions.now()
            db.commit()

        # Remove previous info
        run.log[:] = []
        run.output_files[:] = []

        # Make build directory
        directory = tempfile.mkdtemp('build_%s' % run.experiment_hash)

        container = None
        fq_image_name = '%s/%s' % (
            DOCKER_REGISTRY,
            run.experiment.docker_image,
        )

        try:
            if run.experiment.status != database.Status.BUILT:
                raise ValueError("Experiment to run is not BUILT")

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
                container, run.experiment.docker_image,
            )
            # Turn parameters into a command-line
            cmdline = []
            for k, v in params.items():
                if k.startswith('cmdline_'):
                    i = k[8:]
                    cmdline.extend(['cmd', v, 'run', i])
            cmdline = [
                'docker', 'create', '-i', '--name', container,
                '--', fq_image_name,
            ] + cmdline
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

            # Start container using parameters
            logger.info("Starting container")
            try:
                ret = run_cmd_and_log(
                    db,
                    run.id,
                    ['docker', 'start', '-ai', '--', container],
                )
            except IOError:
                raise ValueError("Got IOError running experiment")
            if ret != 0:
                raise ValueError("Error: Docker returned %d" % ret)
            run.done = functions.now()

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
            run.done = functions.now()
            db.add(database.RunLogLine(run_id=run.id, line=str(e)))
            db.commit()
        finally:
            # Remove container if created
            if container is not None:
                subprocess.call(['docker', 'rm', '-f', '--', container])
            # Remove image
            subprocess.call(['docker', 'rmi', '--', fq_image_name])
            # Remove build directory
            shutil.rmtree(directory)


class K8sRunner(DockerRunner):
    def __init__(self, *, namespace, **kwargs):
        super(K8sRunner, self).__init__(**kwargs)
        self.namespace = namespace

    @classmethod
    def _run_in_pod(cls, namespace, run_id):
        engine, DBSession = database.connect()
        object_store = get_object_store()
        runner = cls(
            namespace=namespace,
            DBSession=DBSession,
            object_store=object_store,
        )
        runner._docker_run(run_id)

    def run_sync(self, run_id):
        kubernetes.config.load_incluster_config()

        name = 'run-{0}'.format(run_id)

        # Create a Kubernetes pod to run
        client = k8s.CoreV1Api()
        cm_var = lambda name, key: k8s.V1EnvVar(
            name=name,
            value_from=k8s.V1EnvVarSource(
                config_map_key_ref=k8s.V1ConfigMapKeySelector(
                    name='config',
                    key=key,
                ),
            ),
        )
        secret_var = lambda name, key: k8s.V1EnvVar(
            name=name,
            value_from=k8s.V1EnvVarSource(
                secret_key_ref=k8s.V1SecretKeySelector(
                    name='reproserver-secret',
                    key=key,
                ),
            ),
        )
        pod = k8s.V1Pod(
            api_version='v1',
            kind='Pod',
            metadata=k8s.V1ObjectMeta(
                name=name,
                labels={
                    'app': 'run',
                    'run': str(run_id),
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
                        name='runner',
                        image='reproserver_web',
                        image_pull_policy='IfNotPresent',
                        args=[
                            'python3', '-c',
                            'from reproserver.run import K8sRunner; ' +
                            'K8sRunner._run_in_pod{0!r}'.format((
                                self.namespace,
                                run_id,
                            )),
                        ],
                        env=[
                            secret_var('SHORTIDS_SALT', 'salt'),
                            secret_var('S3_KEY', 's3_key'),
                            secret_var('S3_SECRET', 's3_secret'),
                            cm_var('S3_URL', 's3.url'),
                            cm_var('S3_BUCKET_PREFIX', 's3.bucket-prefix'),
                            cm_var('S3_CLIENT_URL', 's3.client-url'),
                            secret_var('POSTGRES_USER', 'user'),
                            secret_var('POSTGRES_PASSWORD', 'password'),
                            k8s.V1EnvVar('POSTGRES_HOST', 'postgres'),
                            k8s.V1EnvVar('POSTGRES_DB', 'reproserver'),
                            k8s.V1EnvVar(
                                'DOCKER_HOST',
                                'tcp://127.0.0.1:2375',
                            ),
                            k8s.V1EnvVar('REGISTRY', 'registry:5000'),
                            k8s.V1EnvVar('REPROZIP_USAGE_STATS', 'off'),
                        ],
                    ),
                ],
            ),
        )

        client.create_namespaced_pod(
            namespace=self.namespace,
            body=pod,
        )
        logger.info("Pod created")

        # Watch the pod
        w = kubernetes.watch.Watch()
        f, kwargs = client.list_namespaced_pod, dict(
            namespace=self.namespace,
            label_selector='app=run,run={0}'.format(run_id),
        )
        started = None
        for event in w.stream(f, **kwargs):
            status = event['object'].status
            if not started and status.start_time:
                started = status.start_time
                logger.info("Run pod started: %s", started.isoformat())
            if (status.container_statuses and
                    any(c.state.terminated
                        for c in status.container_statuses)):
                w.stop()
                logger.info("Run pod succeeded")

        # Delete the pod
        client.delete_namespaced_pod(
            name=name,
            namespace=self.namespace,
        )
