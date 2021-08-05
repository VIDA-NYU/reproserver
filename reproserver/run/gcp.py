import asyncio
from google.oauth2 import service_account
import googleapiclient.discovery
import json
import logging
import os
import pkg_resources
import subprocess
import sys
import time

from .base import BaseRunner
from .connector import HttpConnector
from .docker import DockerRunner


logger = logging.getLogger(__name__)


class GcpRunner(BaseRunner):
    """Google Cloud Platform runner implementation.

    This runs a VM on Google Cloud Platform that downloads the bundle and
    inputs and runs the experiment.
    """
    def __init__(self, connector):
        super(GcpRunner, self).__init__(connector)

        credentials = service_account.Credentials.from_service_account_info(
            json.loads(os.environ['GCP_CREDS']),
        )
        self.compute = googleapiclient.discovery.build(
            'compute', 'v1',
            credentials=credentials,
        )
        self.project = os.environ['GCP_PROJECT']
        self.zone = os.environ['GCP_ZONE']
        self.env = os.environ['GCP_ENV']
        self.reproserver_revision = os.environ['GCP_REPO_REVISION']
        self.api_endpoint = os.environ['GCP_API_ENDPOINT']

        # TODO: Watch already-running VMs

    def run_sync(self, run_info):
        # https://github.com/GoogleCloudPlatform/python-docs-samples/tree/master/compute/api

        # Find image
        image_response = self.compute.images().getFromFamily(
            project='ubuntu-os-cloud',
            family='ubuntu-2004-lts',
        ).execute()
        image = image_response['selfLink']

        name = '%s-run-%d' % (self.env, run_info['id'])

        logger.info(
            "Starting Google Cloud VM %r zone=%r project=%r",
            name,
            self.zone,
            self.project,
        )

        with pkg_resources.resource_stream(
            'reproserver',
            'run/gcp-startup-script.sh',
        ) as fp:
            startup_script = fp.read().decode('utf-8')

        vm = {
            'name': name,
            'machineType': 'zones/%s/machineTypes/e2-medium' % self.zone,
            'displayDevice': {
                'enableDisplay': False,
            },
            'metadata': {
                'items': [
                    {
                        'key': 'startup-script',
                        'value': startup_script,
                    },
                    {
                        'key': 'reproserver-run',
                        'value': '%d' % run_info['id'],
                    },
                    {
                        'key': 'reproserver-repo',
                        'value': 'https://github.com/VIDA-NYU/reproserver.git',
                    },
                    {
                        'key': 'reproserver-revision',
                        'value': self.reproserver_revision,
                    },
                    {
                        'key': 'reproserver-api',
                        'value': self.api_endpoint,
                    }
                ],
            },
            'tags': {
                'items': ['reproserver-%s' % self.env],
            },
            'labels': {
                'reproserver-env': self.env,
                'reproserver-run': run_info['id'],
            },
            'disks': [
                {
                    'type': 'PERSISTENT',
                    'boot': True,
                    'mode': 'READ_WRITE',
                    'autoDelete': True,
                    'initializeParams': {
                        'sourceImage': image,
                        'diskType': (
                            'zones/%s/diskTypes/pd-standard' % self.zone
                        ),
                        'diskSizeGb': '50',
                    },
                },
            ],
            'networkInterfaces': [
                {
                    'network': 'global/networks/default',
                    'accessConfigs': [
                        {
                            'name': 'External NAT',
                            'type': 'ONE_TO_ONE_NAT',
                        },
                    ],
                },
            ],
        }
        create_response = self.compute.instances().insert(
            project=self.project,
            zone=self.zone,
            body=vm,
        ).execute()

        # Wait for completion
        while True:
            create_response_status = self.compute.zoneOperations().get(
                project=self.project,
                zone=self.zone,
                operation=create_response['name'],
            ).execute()
            if create_response_status['status'] == 'DONE':
                break
            elif create_response_status['status'] != 'RUNNING':
                raise ValueError(
                    "VM creation failed, operation=%s status=%s" % (
                        create_response['name'],
                        create_response_status['status'],
                    ),
                )
            time.sleep(10)

        logger.info("VM created")

        # TODO: Wait for VM, delete it

    @staticmethod
    def _run_in_vm(api_endpoint, run_id):
        """Entry point in the VM.

        This function is called on the runner VM that is run on GCP, and will
        run the rest of the logic.
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
        runner = DockerRunner(
            HttpConnector(api_endpoint),
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

        # TODO: Proxy

        try:
            asyncio.get_event_loop().run_until_complete(fut)
        except Exception:
            logger.exception("GCP runner VM error")
            raise
        else:
            logger.info("GCP runner VM complete")


Runner = GcpRunner
