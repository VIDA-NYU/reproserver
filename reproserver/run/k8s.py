import asyncio
from datetime import datetime
import kubernetes.client as k8s
import kubernetes.config
import kubernetes.watch
import logging
import os
from sqlalchemy.orm import joinedload
import subprocess
import sys
import time
import yaml

from .. import database
from ..objectstore import get_object_store
from ..proxy import ProxyHandler
from .base import PROM_RUNS
from .docker import DockerRunner


logger = logging.getLogger(__name__)


class InternalProxyHandler(ProxyHandler):
    def select_destination(self):
        # Authentication
        token = self.request.headers.pop('X-Reproserver-Authenticate', None)
        if token != self.application.settings['connection_token']:
            self.set_status(403)
            logger.info("Unauthenticated pod communication")
            self.finish("Unauthenticated pod communication")
            return

        # Read port from hostname
        self.original_host = self.request.host
        host_name = self.request.host_name.split('.', 1)[0]
        run_short_id, port = host_name.split('-')
        port = int(port)

        # TODO: Map Host with `self.application.settings['reproserver_run']`?

        return 'localhost:{0}{1}'.format(port, self.request.uri)

    def alter_request(self, request):
        request.headers['Host'] = self.original_host


class K8sRunner(DockerRunner):
    """Kubernetes runner implementation.

    This talks to the Kubernetes API to create a pod and run the runner there.
    It works similarly to DockerRunner, which it extends, except the bulk of
    the code is executed in that separate "runner" pod instead of the main
    process.
    """
    def __init__(self, **kwargs):
        super(K8sRunner, self).__init__(**kwargs)

        self.config_dir = os.environ['K8S_CONFIG_DIR']

        kubernetes.config.load_incluster_config()

        # Find existing run pods
        client = k8s.CoreV1Api()
        with open(os.path.join(self.config_dir, 'runner.namespace')) as fp:
            namespace = fp.read().strip()
        pods = client.list_namespaced_pod(
            namespace=namespace,
            label_selector='app=run',
        )
        PROM_RUNS.set(0)
        for pod in pods.items:
            run_id = int(pod.metadata.labels['run'], 10)
            logger.info("Attaching to run pod for %d", run_id)
            future = asyncio.get_event_loop().run_in_executor(
                None,
                self._watch_pod,
                client, namespace, run_id,
            )
            future.add_done_callback(self._run_callback(run_id))
            PROM_RUNS.inc()

    def _pod_name(self, run_id):
        return 'run-{0}'.format(run_id)

    @staticmethod
    def _run_in_pod(run_id):
        """Entry point in the runner pod.

        This function is called on the runner pod that is scheduled by
        K8sRunner, and will run the rest of the logic.
        """
        logging.root.handlers.clear()
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

        # Wait for Docker to be available
        for _ in range(30):
            ret = subprocess.call(
                ['docker', 'info'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if ret == 0:
                break
            time.sleep(2)
        else:
            logger.critical("Docker did not come online")
            sys.exit(1)

        # Get a runner from environment
        DBSession = database.connect()
        object_store = get_object_store()
        runner = DockerRunner(
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
            '127.0.0.1',  # Only accept local connections, from the runner pod
        )

        # Also set up a proxy
        proxy = InternalProxyHandler.make_app(
            reproserver_run=run,
            connection_token=os.environ['CONNECTION_TOKEN'],
        )
        proxy.listen(5597, address='0.0.0.0')

        try:
            asyncio.get_event_loop().run_until_complete(fut)
        except Exception:
            logger.exception("Kubernetes runner pod error")
            raise
        else:
            logger.info("Kubernetes runner pod complete")

    def run_sync(self, run_id):
        # This does not run the experiment, it schedules a runner pod by
        # talking to the Kubernetes API. That pod will run the experiment and
        # update the database directly

        kubernetes.config.load_incluster_config()

        name = self._pod_name(run_id)

        # Load configuration from configmap volume
        with open(os.path.join(self.config_dir, 'runner.pod_spec')) as fp:
            pod_spec = yaml.safe_load(fp)
        with open(os.path.join(self.config_dir, 'runner.namespace')) as fp:
            namespace = fp.read().strip()

        # Make required changes
        for container in pod_spec['containers']:
            if container['name'] == 'runner':
                container['args'] += [str(run_id)]

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
        logger.info("Pod created: %s", name)

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
        logger.info("Service created: %s", name)

        self._watch_pod(client, namespace, run_id)

    def _watch_pod(self, client, namespace, run_id):
        name = self._pod_name(run_id)

        w = kubernetes.watch.Watch()
        f, kwargs = client.list_namespaced_pod, dict(
            namespace=namespace,
            label_selector='app=run,run={0}'.format(run_id),
        )
        started = None
        success = False
        for event in w.stream(f, **kwargs):
            if event['type'] == 'DELETED':
                w.stop()
                logger.warning("Run pod was deleted")
                continue
            status = event['object'].status
            if not started and status.start_time:
                started = status.start_time
                logger.info("Run pod started: %s", started.isoformat())
            if (
                status.container_statuses and
                any(c.state.terminated for c in status.container_statuses)
            ):
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
                                '    %s' % line
                                for line in log.splitlines()
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
                run.done = datetime.utcnow()
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


Runner = K8sRunner
