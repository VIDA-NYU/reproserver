import boto3
from flask import Flask, render_template, request
import logging
import os
import pika
from werkzeug.utils import secure_filename


app = Flask(__name__,
            template_folder=os.path.join(os.path.dirname(__file__),
                                         'templates'))

channel = None


@app.route('/')
def index():
    """Landing page from which a user can select an experiment to unpack.
    """
    return render_template('index.html')


@app.route('/unpack', methods=['POST'])
def unpack():
    """Target of the landing page.

    An experiment has been provided, store it and start the build process.
    """
    # Get uploaded file
    uploaded_file = request.files['rpz_file']
    assert uploaded_file.filename
    app.logger.info("Incoming file: %r", uploaded_file.filename)
    filename = secure_filename(uploaded_file.filename)

    # Hash it
    filehash = shahash(uploaded_file)
    app.logger.info("Computed hash: %s", filehash)

    # Rewind it
    uploaded_file.seek(0, SEEK_SET)

    # Check for existence on S3
    s3 = boto3.resource('s3', endpoint_url='http://minio:9000',
                        aws_access_key_id='admin',
                        aws_secret_access_key='hackmehackme')
    if db_update_last_access(filehash):
        app.logger.info("File exists in storage")
    else:
        # Insert it on S3
        s3.Object('uploads', filename).put(Body=uploaded_file)
        app.logger.info("Inserted file in storage")

        # Insert it in database
        db_insert(filehash, filename)

    # Encode hash + filename for permanent URL
    permanent_id = base64.urlsafe_b64encode(filehash + '|' + filename)

    # Redirect to build page
    return redirect(TEMPORARY, '/reproduce/' + permanent_id)


@app.route('/reproduce/(?P<experiment>[A-Za-z0-9_-]+)')
def reproduce():
    """Show build log and ask for run parameters.
    """
    # Decode info from URL
    permanent_id = base64.urlsafe_b64decode(request.args['experiment'])
    sep = permanent_id.find('|')
    if sep != -1:
        filehash = permanent_id[:sep]
        filename = permanent_id[sep + 1:]

    # Look up file in database
    record = db_get(filehash)  # Also updates last access
    if record is None:
        return render_template(404, 'setup_notfound.html',
                               filename=filename)

    # JSON endpoint, returns data for the page's JavaScript to update itself
    if accept_json:
        log_from = request.args.get('log_from', 0)
        return json({'status': record.status,
                     'log': record.log(log_from),
                     'params': record.params})
    # HTML view, return the page
    else:
        # If it's done building, send build log and run form
        if record.status == 'BUILT':
            return render_template('setup.html', filename=filename,
                                   built=True, error=False,
                                   log=record.log(0), params=record.params)
        if record.status == 'ERROR':
            return render_template('setup.html', filename=filename,
                                   built=True, error=True,
                                   log=record.log(0))
        # If it's currently building, show the log
        elif record.status == 'BUILDING':
            return render_template('setup.html', filename=filename,
                                   built=False, log=record.log(0))
        # Else, trigger the build
        else:
            if record.status == 'NOBUILD':
                db_set_queued(filehash)  # set status = 'QUEUED'
                channel.basic_publish('', routing_key='build_queue',
                                      body=filehash)
            return render_template('setup.html', filename=filename,
                                   built=False)


@app.route('/run/(?P<experiment>[A-Za-z0-9_-]+)', methods=['POST'])
def run():
    """Gets the run parameters POSTed to from /reproduce.

    Triggers the run and redirects to the results page.
    """
    # Get experiment info
    filehash = request.args['filehash']
    filename = request.args['filename']

    # Get run parameters
    params = {}
    for k, v in request.args.iteritems():
        if k.startswith('param_'):
            params[k[6:]] = v

    # TODO: Trigger run
    return ("Not yet implemented: run experiment {filehash}\n"
            "Parameters:\n{params}\n").format(
                filehash=filehash,
                params="  (no parameters)" if not params else
                '\n'.join("  - {k}: {v}".format(k=k, v=v)
                          for k, v in params.iteritems()))


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
