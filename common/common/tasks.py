import logging
import os
import pika
import pika.exceptions
import time


class TaskQueues(object):
    def __init__(self):
        self._connect()

    def _connect(self):
        logging.info("Connecting to AMQP broker")
        self.connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=os.environ['AMQP_HOST'],
            credentials=pika.PlainCredentials(os.environ['AMQP_USER'],
                                              os.environ['AMQP_PASSWORD'])))
        self.channel = self.connection.channel()

        self.channel.queue_declare(queue='build_queue', durable=True)
        self.channel.queue_declare(queue='run_queue', durable=True)

        self.channel.basic_qos(prefetch_count=1)

    def _retry(self, f):
        while True:
            try:
                return f()
            except pika.exceptions.ConnectionClosed:
                logging.exception("AMQP connection is down...")
                time.sleep(1)
            self._connect()

    def publish_build_task(self, body):
        self._retry(
            lambda: self.channel.basic_publish('',
                                               routing_key='build_queue',
                                               body=body.encode('utf-8')))

    def publish_run_task(self, body):
        self._retry(
            lambda: self.channel.basic_publish('',
                                               routing_key='run_queue',
                                               body=body.encode('utf-8')))

    def consume_build_tasks(self, callback):
        self.channel.basic_consume(callback, queue='build_queue')
        self._retry(self.channel.start_consuming)

    def consume_run_tasks(self, callback):
        self.channel.basic_consume(callback, queue='run_queue')
        self._retry(self.channel.start_consuming)
