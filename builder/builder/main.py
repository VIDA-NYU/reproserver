import logging
import pika


def build(channel, method, properties, body):
    logging.info("Build request received: %r, %r", body, properties)
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main():
    logging.basicConfig(level=logging.INFO)

    logging.info("Connecting to AMQP broker")
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host='reproserver_rabbitmq',
        credentials=pika.PlainCredentials('admin', 'hackme')))
    channel = connection.channel()

    channel.queue_declare(queue='build_queue', durable=True)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(build, queue='build_queue')

    logging.info("Ready, listening for requests")
    channel.start_consuming()
