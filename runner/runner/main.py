import logging
import pika


def run(channel, method, properties, body):
    logging.info("Run request received: %r, %r", body, properties)
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main():
    logging.basicConfig(level=logging.INFO)

    logging.info("Connecting to AMQP broker")
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host='reproserver-rabbitmq',
        credentials=pika.PlainCredentials('admin', 'hackme')))
    channel = connection.channel()

    channel.queue_declare(queue='run_queue', durable=True)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(run, queue='run_queue')

    logging.info("Ready, listening for requests")
    channel.start_consuming()
