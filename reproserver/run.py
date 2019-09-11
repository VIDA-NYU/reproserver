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
import sys
import tempfile
import time
import yaml

from reproserver import database
from reproserver.objectstore import get_object_store
from reproserver.proxy import ProxyHandler
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
        line = line.rstrip()
        logger.info("> %s", line)
        session.add(database.RunLogLine(
            run_id=run_id,
            line=line,
        ))
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
                     joinedload(database.Run.ports),
                     exp.joinedload(database.Experiment.parameters),
                     exp.joinedload(database.Experiment.paths))
        ).get(run_id)
        if run is None:
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
            cmdline = [
                'docker', 'create', '-i', '--name', container,
            ]
            for port in run.ports:
                cmdline.extend([
                    '-p', '127.0.0.1:{0}:{0}'.format(port.port_number),
                ])
            cmdline.extend([
                '--', fq_image_name,
            ])
            for k, v in params.items():
                if k.startswith('cmdline_'):
                    i = k[8:]
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


class InternalProxyHandler(ProxyHandler):
    def select_destination(self):
        # Authentication
        token = self.request.headers.pop('X-Reproserver-Authenticate', None)
        if token != 'secret-token':
            self.set_status(403)
            logger.info("Unauthenticated pod communication")
            self.finish("Unauthenticated pod communication")
            return

        # Read port from hostname
        self.original_host = self.request.host
        host_name = self.request.host_name.split('.', 1)[0]
        run_short_id, port = host_name.split('-')
        port = int(port)

        # TODO: Map Host value from `self.application.reproserver_run`?

        return 'localhost:{0}{1}'.format(port, self.request.uri)

    def alter_request(self, request):
        request.headers['Host'] = self.original_host


class K8sRunner(DockerRunner):
    @classmethod
    def _run_in_pod(cls, run_id):
        logging.root.handlers.clear()
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s: %(message)s")

        # Get a runner from environment
        engine, DBSession = database.connect()
        object_store = get_object_store()
        runner = cls(
            DBSession=DBSession,
            object_store=object_store,
        )

        # Load run information
        db = DBSession()
        run = (
            db.query(database.Run)
            .options(joinedload(database.Run.ports))
        ).get(run_id)
        if run is None:
            logger.critical("Cannot find run %d in database", run_id)
            sys.exit(1)

        # Run
        fut = asyncio.get_event_loop().run_in_executor(
            None,
            runner._docker_run,
            run_id,
        )

        # Also set up a proxy
        proxy = InternalProxyHandler.make_app(reproserver_run=run)
        proxy.listen(5597, address='0.0.0.0')

        asyncio.get_event_loop().run_until_complete(fut)

    def run_sync(self, run_id):
        kubernetes.config.load_incluster_config()

        name = 'run-{0}'.format(run_id)

        # Load configuration from configmap volume
        with open('/etc/k8s-config/runner.pod_spec') as fp:
            pod_spec = yaml.safe_load(fp)
        with open('/etc/k8s-config/runner.namespace') as fp:
            namespace = fp.read().strip()

        # Make required changes
        for container in pod_spec['containers']:
            if container['name'] == 'runner':
                container['args'] = [
                    'python3', '-c',
                    'from reproserver.run import K8sRunner; ' +
                    'K8sRunner._run_in_pod{0!r}'.format((
                        run_id,
                    )),
                ]

        # Create a Kubernetes pod to run
        client = k8s.CoreV1Api()
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
            spec=pod_spec,
        )
        client.create_namespaced_pod(
            namespace=namespace,
            body=pod,
        )
        logger.info("Pod created")

        # Create a service for proxy connections
        svc = k8s.V1Service(
            api_version='v1',
            kind='Service',
            metadata=k8s.V1ObjectMeta(
                name=name,
                labels={
                    'app': 'run',
                    'run': str(run_id),
                },
            ),
            spec=k8s.V1ServiceSpec(
                selector={
                    'app': 'run',
                    'run': str(run_id),
                },
                ports=[
                    k8s.V1ServicePort(
                        protocol='TCP',
                        port=5597,
                    ),
                ],
            ),
        )
        client.create_namespaced_service(
            namespace=namespace,
            body=svc,
        )
        logger.info("Service created")

        # Watch the pod
        w = kubernetes.watch.Watch()
        f, kwargs = client.list_namespaced_pod, dict(
            namespace=namespace,
            label_selector='app=run,run={0}'.format(run_id),
        )
        started = None
        success = False
        for event in w.stream(f, **kwargs):
            status = event['object'].status
            if not started and status.start_time:
                started = status.start_time
                logger.info("Run pod started: %s", started.isoformat())
            if (status.container_statuses and
                    any(c.state.terminated
                        for c in status.container_statuses)):
                w.stop()

                # Check the status of all containers
                for container in status.container_statuses:
                    terminated = container.state.terminated
                    if terminated:
                        exit_code = terminated.exit_code
                        if container.name == 'runner' and exit_code == 0:
                            logger.info("Run pod succeeded")
                            success = True
                        elif exit_code is not None:
                            # Log any container that exited, including runner
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
            logger.warning("Run %d failed", run_id)
            db = self.DBSession()
            run = db.query(database.Run).get(run_id)
            if run is None:
                logger.warning("Run not in database, can't set status")
            else:
                run.done = functions.now()
                db.commit()

        # Delete the pod and service
        time.sleep(60)
        client.delete_namespaced_pod(
            name=name,
            namespace=namespace,
        )
        client.delete_namespaced_service(
            name=name,
            namespace=namespace,
        )
