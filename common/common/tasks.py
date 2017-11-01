import logging
import os
import pika
import pika.exceptions
import Queue
import threading
import time


class TaskQueues(object):
    def __init__(self):
        self._connection = None
        self._consume_queue = None
        self._lock = threading.Lock()
        self._callback_queue = Queue.Queue()
        self._thread = threading.Thread(target=self._loop)
        self._thread.start()

    def _loop(self):
        while True:
            with self._lock:
                try:
                    if not self._connection:
                        self._connect()
                    else:
                        self._connection.process_data_events()
                except pika.exceptions.ConnectionClosed:
                    logging.exception("AMQP connection is down...")
                    self._connection = None
            time.sleep(2)

    def _connect(self):
        logging.info("Connecting to AMQP broker")
        self._connection = pika.BlockingConnection(pika.ConnectionParameters(
            heartbeat_interval=20,
            host=os.environ['AMQP_HOST'],
            credentials=pika.PlainCredentials(os.environ['AMQP_USER'],
                                              os.environ['AMQP_PASSWORD'])))
        self.channel = self._connection.channel()

        self.channel.queue_declare(queue='build_queue', durable=True)
        self.channel.queue_declare(queue='run_queue', durable=True)

        self.channel.basic_qos(prefetch_count=1)

        if self._consume_queue:
            self.channel.basic_consume(self._callback,
                                       queue=self._consume_queue)

    def _retry(self, f):
        while True:
            with self._lock:
                if self._connection is not None:
                    try:
                        return f()
                    except pika.exceptions.ConnectionClosed:
                        logging.exception("AMQP connection is down...")
                        self._connection = None
            time.sleep(2)

    def publish_build_task(self, body):
        self._retry(
            lambda: self.channel.basic_publish('',
                                               routing_key='build_queue',
                                               body=body))

    def publish_run_task(self, body):
        self._retry(
            lambda: self.channel.basic_publish('',
                                               routing_key='run_queue',
                                               body=body))

    def _callback(self, channel, method, _properties, body):
        self._callback_queue.put(body)
        channel.basic_ack(delivery_tag=method.delivery_tag)

    def _consume(self, callback, queue):
        with self._lock:
            self._consume_queue = queue
            if self._connection is not None:
                self.channel.basic_consume(self._callback, queue=queue)

        while True:
            body = self._callback_queue.get(block=True)
            if body is None:
                break
            try:
                callback(body)
            except Exception as e:
                logging.exception("Uncaught exception in task handler")

    def consume_build_tasks(self, callback):
        self._consume(callback, 'build_queue')

    def consume_run_tasks(self, callback):
        self._consume(callback, 'run_queue')
