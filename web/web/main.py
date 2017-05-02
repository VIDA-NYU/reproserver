import boto3
from flask import Flask, render_template, request
import logging
import os
import pika
from werkzeug.utils import secure_filename


app = Flask(__name__,
            template_folder=os.path.join(os.path.dirname(__file__),
                                         'templates'))


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/unpack', methods=['POST'])
def unpack():
    rpz_file = request.files['rpz_file']
    assert rpz_file.filename
    app.logger.info("Incoming file: %r", rpz_file.filename)
    filename = secure_filename(rpz_file.filename)

    s3 = boto3.resource('s3', endpoint_url='http://minio:9000',
                        aws_access_key_id='admin',
                        aws_secret_access_key='hackmehackme')
    s3.Object('uploads', filename).put(Body=rpz_file)
    app.logger.info("Uploaded file: %r", filename)

    channel.basic_publish('', routing_key='build_queue', body=filename)
    return render_template('unpack.html', filename=filename)


def main():
    logging.basicConfig(level=logging.INFO)

    logging.info("Connecting to AMQP broker")
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host='rabbitmq', credentials=pika.PlainCredentials('admin', 'hackme')))
    global channel
    channel = connection.channel()

    channel.queue_declare(queue='build_queue', durable=True)

    app.logger.info("web running")
    app.run(host="0.0.0.0", port=8000, debug=True)
