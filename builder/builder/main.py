import boto3
import database
import logging
import os
import pika
import shutil
import tempfile
import time


SQLSession = None
s3 = None


def build(channel, method, properties, body):
    """Process a build task.

    Lookup the experiment in the database, and the file on S3. Then, do the
    build, upload the log, and fill in the parameters in the database.
    """
    logging.info("Build request received: %r, %r", body, properties)

    # Look up the experiment in the database
    session = SQLSession()
    experiment = session.query(database.Experiment).get(body)
    if not experiment:
        logging.error("Got a build request but couldn't get the experiment "
                      "from the database (body=%r)", body)
        # ACK anyway
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    # Update status in database
    if experiment.status != database.Status.QUEUED:
        logging.warning("Building experiment which has status %r",
                        experiment.status)
    experiment.status = database.Status.BUILDING
    session.commit()
    logging.info("Set status to BUILDING")

    # Make build directory
    directory = tempfile.mkdtemp('build_%s' % experiment.hash)

    try:
        # Get experiment file
        logging.info("Downloading file...")
        local_path = os.path.join(directory, 'experiment.rpz')
        s3.Bucket('experiments').download_file(experiment.hash, local_path)
        logging.info("Got file, %d bytes", os.stat(local_path).st_size)

        # TODO: Build the experiment
        session.add(database.BuildLogLine(experiment_hash=experiment.hash,
                                          line="Preparing the build"))
        session.commit()
        time.sleep(10)
        session.add(database.BuildLogLine(experiment_hash=experiment.hash,
                                          line="Finishing the build"))
        session.commit()
        # Add parameters
        session.add(database.Parameter(experiment_hash=experiment.hash,
                                       name="One", optional=False))
        session.add(database.Parameter(experiment_hash=experiment.hash,
                                       name="Two", optional=True))

        logging.info("Build over, finishing up")
        # Set status
        experiment.status = database.Status.BUILT
        # ACK
        channel.basic_ack(delivery_tag=method.delivery_tag)
        session.commit()
        logging.info("Done!")
    except Exception:
        logging.exception("Error processing build!")
        # Set database status back to QUEUED
        experiment.status = database.Status.QUEUED
        session.commit()
        # NACK the task in RabbitMQ
        channel.basic_nack(delivery_tag=method.delivery_tag)
        # Remove build directory
        shutil.rmtree(directory)


def main():
    logging.basicConfig(level=logging.INFO)

    # SQL database
    global SQLSession
    logging.info("Connecting to SQL database")
    engine, SQLSession = database.connect()

    # AMQP
    logging.info("Connecting to AMQP broker")
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host='reproserver-rabbitmq', heartbeat_interval=5,
        credentials=pika.PlainCredentials('admin', 'hackme')))
    channel = connection.channel()

    channel.queue_declare(queue='build_queue', durable=True)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(build, queue='build_queue')

    # Object storage
    global s3
    s3 = boto3.resource('s3', endpoint_url='http://reproserver-minio:9000',
                        aws_access_key_id='admin',
                        aws_secret_access_key='hackmehackme')

    # Wait for tasks
    logging.info("Ready, listening for requests")
    channel.start_consuming()
