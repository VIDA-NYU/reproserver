from common import TaskQueues
from common.utils import setup_logging
import logging


def run(channel, method, properties, body):
    logging.info("Run request received: %r, %r", body, properties)
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main():
    setup_logging('REPROSERVER-RUNNER')

    # AMQP
    tasks = TaskQueues()

    logging.info("Ready, listening for requests")
    tasks.consume_run_tasks(run)
