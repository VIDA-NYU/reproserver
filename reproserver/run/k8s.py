import asyncio
import kubernetes_asyncio.client as k8s_client
import kubernetes_asyncio.config as k8s_config
import kubernetes_asyncio.watch as k8s_watch
import logging
import os
import subprocess
import sys
import time
import yaml

from .connector import DirectConnector, HttpConnector
from .. import database
from ..proxy import ProxyHandler
from ..utils import background_future, setup
from .base import PROM_RUNS, BaseRunner
from .docker import DockerRunner


logger = logging.getLogger(__name__)


def labels_to_string(labels_dict):
    return ','.join(k + '=' + v for k, v in labels_dict.items())


class InternalProxyHandler(ProxyHandler):
    """Runs in the run pod, forwards traffic to the Docker container.

    This runs in the "runner" container of the run pod and forwards traffic
    from the main proxy to the Docker container, which has exposed ports on
    localhost.
    """
    def select_destination(self):
        # Authentication
        token = self.request.headers.pop('X-Reproserver-Authenticate', None)
        if token != self.application.settings['connection_token']:
            self.set_status(403)
            logger.info("Unauthenticated pod communication")
            self.finish("Unauthenticated pod communication")
            return

        self.original_host = self.request.host
        if 'X-Reproserver-Port' in self.request.headers:
            port = int(self.request.headers['X-Reproserver-Port'])
        else:
            # Read port from hostname
            host_name = self.request.host_name.split('.', 1)[0]
            run_short_id, port = host_name.split('-')
            port = int(port)

        # TODO: Map Host with `self.application.settings['reproserver_run']`?

        return 'localhost:{0}{1}'.format(port, self.request.uri)

    def alter_request(self, request):
        request.headers['Host'] = self.original_host
        request.headers.pop('X-Reproserver-Port', None)


class K8sRunner(BaseRunner):
    """Kubernetes runner implementation.

    This talks to the Kubernetes API to create a pod and run the runner there.
    It works similarly to DockerRunner, which it extends, except the bulk of
    the code is executed in that separate "runner" pod instead of the main
    process.
    """
    def __init__(self, connector):
        super(K8sRunner, self).__init__(connector)

        self.config_dir = os.environ['K8S_CONFIG_DIR']
        self.namespace = os.environ['RUN_NAMESPACE']
        self.pod_name_prefix = os.environ['RUN_NAME_PREFIX']
        self.pod_labels = yaml.safe_load(os.environ['RUN_LABELS'])

    def _pod_name(self, run_id):
        return '{0}run-{1}'.format(self.pod_name_prefix, run_id)

    async def run_inner(self, run_info):
        run_id = run_info['id']
        del run_info

        # This does not run the experiment, it schedules a runner pod by
        # talking to the Kubernetes API. That pod will run the experiment and
        # update the database directly

        k8s_config.load_incluster_config()

        name = self._pod_name(run_id)

        # Load configuration from configmap volume
        with open(os.path.join(self.config_dir, 'runner-pod-spec.yaml')) as fp:
            pod_spec = yaml.safe_load(fp)

        # Make required changes
        for container in pod_spec['containers']:
            if container['name'] == 'runner':
                container['args'] += [str(run_id)]

                # This is mostly used by Tilt
                if os.environ.get('OVERRIDE_RUNNER_IMAGE'):
                    container['image'] = os.environ['OVERRIDE_RUNNER_IMAGE']

        async with k8s_client.ApiClient() as api:
            # Create a Kubernetes pod to run
            v1 = k8s_client.CoreV1Api(api)
            pod = k8s_client.V1Pod(
                api_version='v1',
                kind='Pod',
                metadata=k8s_client.V1ObjectMeta(
                    name=name,
                    labels=self.pod_labels | {
                        'app': 'run',
                        'run': str(run_id),
                    },
                ),
                spec=pod_spec,
            )
            await v1.create_namespaced_pod(
                namespace=self.namespace,
                body=pod,
            )
            logger.info("Pod created: %s", name)

            # Create a service for proxy connections
            svc = k8s_client.V1Service(
                api_version='v1',
                kind='Service',
                metadata=k8s_client.V1ObjectMeta(
                    name=name,
                    labels=self.pod_labels | {
                        'app': 'run',
                        'run': str(run_id),
                    },
                ),
                spec=k8s_client.V1ServiceSpec(
                    selector=self.pod_labels | {
                        'app': 'run',
                        'run': str(run_id),
                    },
                    ports=[
                        k8s_client.V1ServicePort(
                            protocol='TCP',
                            port=5597,
                        ),
                    ],
                ),
            )
            await v1.create_namespaced_service(
                namespace=self.namespace,
                body=svc,
            )
            logger.info("Service created: %s", name)


Runner = K8sRunner


def _run_in_pod(run_id):
    """Entry point in the runner pod.

    This function is called on the runner pod that is scheduled by
    K8sRunner, and will run the rest of the logic.
    """
    setup(enable_prometheus=False)

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
    runner = DockerRunner(
        HttpConnector(
            os.environ['API_ENDPOINT'],
            os.environ['CONNECTION_TOKEN'],
        ),
    )

    # Load run information
    run_info = asyncio.get_event_loop().run_until_complete(
        runner.connector.init_run_get_info(run_id),
    )

    # Run
    fut = runner._docker_run(
        run_info,
        '127.0.0.1',  # Only accept local connections, from the runner pod
    )

    # Also set up a proxy
    proxy = InternalProxyHandler.make_app(
        reproserver_run=run_info,
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


class K8sWatcher(object):
    """Background process watching run pods.

    * Deletes them when done, setting potential error status
    * Sets Prometheus metrics
    """
    def __init__(self, connector):
        self.connector = connector
        self.loop = asyncio.get_event_loop()
        self.config_dir = os.environ['K8S_CONFIG_DIR']
        self.running = set()
        self.namespace = os.environ['RUN_NAMESPACE']
        self.pod_labels = yaml.safe_load(os.environ['RUN_LABELS'])
        self.label_selector = labels_to_string(
            self.pod_labels | {'app': 'run'}
        )
        logger.info("Using label selector: %s", self.label_selector)
        self.pod_name_prefix = os.environ['RUN_NAME_PREFIX']

    def running_set_add(self, run_id):
        if run_id in self.running:
            return
        self.running.add(run_id)
        PROM_RUNS.set(len(self.running))
        logger.info(
            "Running: %s",
            ', '.join('%d' % i for i in sorted(self.running)),
        )

    def running_set_discard(self, run_id):
        if run_id not in self.running:
            return
        self.running.discard(run_id)
        PROM_RUNS.set(len(self.running))
        logger.info(
            "Running: %s",
            ', '.join('%d' % i for i in sorted(self.running)),
        )

    async def watch(self):
        DBSession = self.connector.DBSession

        k8s_config.load_incluster_config()

        async with k8s_client.ApiClient() as api:
            v1 = k8s_client.CoreV1Api(api)

            await self._full_sync(api)

            # Watch changes
            watch = k8s_watch.Watch()
            f, kwargs = v1.list_namespaced_pod, dict(
                namespace=self.namespace,
                label_selector=self.label_selector,
            )
            while True:
                try:
                    async for event in watch.stream(f, **kwargs):
                        await self._handle_watch_event(api, DBSession, event)
                except k8s_client.ApiException as e:
                    if e.status != 410:
                        raise

    async def _full_sync(self, api):
        logger.info("Doing full sync")

        v1 = k8s_client.CoreV1Api(api)

        # Find existing run pods
        pods = await v1.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=self.label_selector,
        )
        for pod in pods.items:
            run_id = int(pod.metadata.labels['run'], 10)
            self.running.add(run_id)
            logger.info("Found run pod for %d", run_id)
            await self._check_pod(api, run_id, pod)
        PROM_RUNS.set(len(self.running))

        # Find existing services
        services = await v1.list_namespaced_service(
            namespace=self.namespace,
            label_selector=self.label_selector,
        )
        for service in services.items:
            run_id = int(service.metadata.labels['run'], 10)
            if run_id not in self.running:
                # Delete service with no matching pod
                logger.info("Removing service %s", service.metadata.name)
                try:
                    await v1.delete_namespaced_service(
                        name=service.metadata.name,
                        namespace=self.namespace,
                    )
                except k8s_client.ApiException as e:
                    if e.status != 404:
                        raise

        logger.info(
            "Full sync complete, %d pods: %s",
            len(self.running),
            ', '.join('%d' % i for i in sorted(self.running)),
        )

    async def _handle_watch_event(self, api, DBSession, event):
        pod = event['object']
        try:
            run_id = int(pod.metadata.labels['run'], 10)
        except (KeyError, ValueError):
            logger.warning(
                "Invalid pod '%s' doesn't have run label",
                pod.metadata.name,
            )
            return

        logger.info("Event: %s %s", event['type'], pod.metadata.name)

        if (
            event['type'] != 'DELETED'
            and pod.metadata.deletion_timestamp is not None
        ):
            # Ignore, pod is being deleted
            logger.info("(ignore, pod is being deleted)")
            return

        # Get run
        db = DBSession()
        run = db.query(database.Run).get(run_id)
        if run is None:
            logger.warning("Event in pod for unknown run %d", run_id)
            return

        if event['type'] == 'DELETED':
            self.running_set_discard(run_id)

            logger.info("Run pod for %d deleted", run_id)
            if run.done is None:
                logger.warning("Run pod deleted but run wasn't set as done!")
                await self.connector.run_failed(run_id, "Internal error")
            try:
                await k8s_client.CoreV1Api(api).delete_namespaced_service(
                    name='%srun-%d' % (self.pod_name_prefix, run_id),
                    namespace=self.namespace,
                )
            except k8s_client.ApiException as e:
                if e.status != 404:
                    raise
        else:
            await self._check_pod(api, run_id, pod)

    async def _check_pod(self, api, run_id, pod):
        v1 = k8s_client.CoreV1Api(api)

        async def wait_then_delete_pod(name):
            await asyncio.sleep(60)

            logger.info("Cleaning up %s", name)
            try:
                await v1.delete_namespaced_pod(
                    name=name,
                    namespace=self.namespace,
                )
            except k8s_client.ApiException as e:
                if e.status != 404:
                    raise
            try:
                await v1.delete_namespaced_service(
                    name=name,
                    namespace=self.namespace,
                )
            except k8s_client.ApiException as e:
                if e.status != 404:
                    raise

        if (
            pod.status.container_statuses
            and any(c.state.terminated for c in pod.status.container_statuses)
        ):
            self.running_set_discard(run_id)

            # Check the status of all containers
            success = False
            for container in pod.status.container_statuses:
                terminated = container.state.terminated
                if terminated:
                    exit_code = terminated.exit_code
                    if container.name == 'runner' and exit_code == 0:
                        logger.info("Runner pod completed for run %d", run_id)
                        success = True
                    elif exit_code is not None:
                        # Log any container that exited, including
                        # runner if code is not zero
                        log = await v1.read_namespaced_pod_log(
                            pod.metadata.name,
                            self.namespace,
                            container=container.name,
                            tail_lines=300,
                        )
                        log = '\n'.join(
                            '    %s' % line
                            for line in log.splitlines()
                        )
                        logger.warning(
                            "Pod %s container %s exited with %d\n%s",
                            pod.metadata.name,
                            container.name,
                            exit_code,
                            log
                        )

            if not success:
                await self.connector.run_failed(run_id, "Internal error")

            # Schedule deletion in 1 minute
            background_future(wait_then_delete_pod(pod.metadata.name))
        else:
            self.running_set_add(run_id)


def watch():
    setup()

    watcher = K8sWatcher(DirectConnector(
        DBSession=database.connect(create=True),
        object_store=None,
    ))
    asyncio.run(watcher.watch())
