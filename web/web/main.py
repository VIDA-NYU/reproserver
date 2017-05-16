import base64
from common import database, TaskQueues, get_object_store
from flask import Flask, jsonify, redirect, render_template, request, url_for
from hashlib import sha256
import logging
from sqlalchemy.sql import functions
from werkzeug.utils import secure_filename


app = Flask(__name__)


# SQL database
engine, SQLSession = database.connect()

if not engine.dialect.has_table(engine.connect(), 'experiments'):
    logging.warning("The tables don't seem to exist; creating")
    from common.database import Base

    Base.metadata.create_all(bind=engine)


# AMQP
tasks = TaskQueues()


# Object storage
object_store = get_object_store()

it = iter(object_store.buckets.all())
try:
    next(it)
except StopIteration:
    for name in ['experiments', 'inputs', 'outputs']:
        object_store.create_bucket(Bucket=name)


@app.route('/')
def index():
    """Landing page from which a user can select an experiment to unpack.
    """
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
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
    hasher = sha256()
    chunk = uploaded_file.read(4096)
    while chunk:
        hasher.update(chunk)
        chunk = uploaded_file.read(4096)
    filehash = hasher.hexdigest()
    app.logger.info("Computed hash: %s", filehash)

    # Rewind it
    uploaded_file.seek(0, 0)

    # Check for existence of experiment
    session = SQLSession()
    experiment = session.query(database.Experiment).get(filehash)
    if experiment:
        experiment.last_access = functions.now()
        app.logger.info("File exists in storage")
    else:
        # Insert it on S3
        object_store.Object('experiments', filehash).put(Body=uploaded_file)
        app.logger.info("Inserted file in storage")

        # Insert it in database
        experiment = database.Experiment(hash=filehash)
        session.add(experiment)
    session.add(database.Upload(experiment=experiment,
                                filename=filename,
                                submitted_ip=request.remote_addr))
    session.commit()

    # Encode hash + filename for permanent URL
    experiment_code = base64.urlsafe_b64encode(filehash + '|' + filename)

    # Redirect to build page
    return redirect(url_for('reproduce', experiment_code=experiment_code), 302)


@app.route('/reproduce/<experiment_code>')
def reproduce(experiment_code):
    """Show build log and ask for run parameters.
    """
    # Decode info from URL
    app.logger.info("Decoding %r", experiment_code)
    try:
        permanent_id = base64.urlsafe_b64decode(
            experiment_code.encode('ascii'))
        sep = permanent_id.index('|')
    except Exception:
        return render_template('setup_notfound.html'), 404
    filehash = permanent_id[:sep]
    filename = permanent_id[sep + 1:]

    # Look up the experiment in database
    session = SQLSession()
    experiment = session.query(database.Experiment).get(filehash)
    if not experiment:
        return render_template('setup_notfound.html'), 404
    # Also updates last access
    experiment.last_access = functions.now()

    try:
        # JSON endpoint, returns data for JavaScript to update the page
        if (request.accept_mimetypes.best_match(['application/json',
                                                 'text/html']) ==
                'application/json'):
            log_from = request.args.get('log_from', 0)
            return jsonify({'status': experiment.status,
                            'log': experiment.get_log(log_from),
                            'params': experiment.parameters})
        # HTML view, return the page
        else:
            # If it's done building, send build log and run form
            if experiment.status == database.Status.BUILT:
                app.logger.info("Experiment already built")
                input_files = (
                    session.query(database.Path)
                    .filter(database.Path.experiment_hash == experiment.hash)
                    .filter(database.Path.is_input)).all()
                return render_template('setup.html', filename=filename,
                                       built=True, error=False,
                                       log=experiment.get_log(0),
                                       params=experiment.parameters,
                                       input_files=input_files,
                                       experiment_code=experiment_code)
            if experiment.status == database.Status.ERROR:
                app.logger.info("Experiment is errored")
                return render_template('setup.html', filename=filename,
                                       built=True, error=True,
                                       log=experiment.get_log(0),
                                       experiment_code=experiment_code)
            # If it's currently building, show the log
            elif experiment.status == database.Status.BUILDING:
                app.logger.info("Experiment is currently building")
                return render_template('setup.html', filename=filename,
                                       built=False, log=experiment.get_log(0),
                                       experiment_code=experiment_code)
            # Else, trigger the build
            else:
                if experiment.status == database.Status.NOBUILD:
                    app.logger.info("Triggering a build, sending message")
                    experiment.status = database.Status.QUEUED
                    tasks.publish_build_task(filehash)
                return render_template('setup.html', filename=filename,
                                       built=False,
                                       experiment_code=experiment_code)
    finally:
        session.commit()


@app.route('/run/<experiment_code>', methods=['POST'])
def run(experiment_code):
    """Gets the run parameters POSTed to from /reproduce.

    Triggers the run and redirects to the results page.
    """
    # Decode info from URL
    app.logger.info("Decoding %r", experiment_code)
    try:
        permanent_id = base64.urlsafe_b64decode(
            experiment_code.encode('ascii'))
        sep = permanent_id.index('|')
    except Exception:
        return render_template('setup_notfound.html'), 404
    filehash = permanent_id[:sep]
    filename = permanent_id[sep + 1:]

    # Look up the experiment in database
    session = SQLSession()
    experiment = session.query(database.Experiment).get(filehash)
    if not experiment:
        return render_template('setup_notfound.html'), 404

    # New run entry
    try:
        run = database.Run(experiment_hash=filehash,
                           experiment_filename=filename)
        session.add(run)

        # Get list of parameters
        params = set()
        params_unset = set()
        for param in experiment.parameters:
            if not param.optional:
                params_unset.add(param.name)
            params.add(param.name)

        # Get run parameters
        for k, v in request.form.iteritems():
            if k.startswith('param_'):
                name = k[6:]
                if name not in params:
                    raise ValueError("Unknown parameter %s" % k)
                run.parameter_values.append(database.ParameterValue(name=name,
                                                                    value=v))
                params_unset.discard(name)

        if params_unset:
            raise ValueError("Missing value for parameters: %s" %
                             ", ".join(params_unset))

        # Get list of input files
        input_files = set(p.name for p in session.query(database.Path)
                          .filter(database.Path.experiment_hash == filehash)
                          .filter(database.Path.is_input).all())

        # Get input files
        for k, uploaded_file in request.files.iteritems():
            if not uploaded_file:
                continue

            if not k.startswith('inputfile_') or k[10:] not in input_files:
                raise ValueError("Unknown input file %s" % k)

            name = k[10:]
            app.logger.info("Incoming input file: %s", name)

            # Hash file
            hasher = sha256()
            chunk = uploaded_file.read(4096)
            while chunk:
                hasher.update(chunk)
                chunk = uploaded_file.read(4096)
            inputfilehash = hasher.hexdigest()
            app.logger.info("Computed hash: %s", inputfilehash)

            # Rewind it
            filesize = uploaded_file.tell()
            uploaded_file.seek(0, 0)

            # Insert it on S3
            object_store.Object('inputs', inputfilehash).put(
                Body=uploaded_file)
            app.logger.info("Inserted file in storage")

            # Insert it in database
            input_file = database.InputFile(hash=inputfilehash, name=name,
                                            size=filesize)
            run.input_files.append(input_file)

        # Trigger run
        session.commit()
        tasks.publish_run_task(str(run.id))

        # Redirect to results page
        return redirect(url_for('results', run_id=run.id), 302)
    except Exception:
        session.rollback()
        raise


@app.route('/results/<run_id>')
def results(run_id):
    """Shows the results of a run, whether it's done or in progress.
    """
    # TODO: /results view
    return (
        "Not yet implemented: results for run #{run}\n".format(run=run_id),
        200,
        {'Content-Type': 'text/plain'},
    )


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/data')
def data():
    """Print some system information.
    """
    session = SQLSession()
    return render_template(
        'data.html',
        experiments=session.query(database.Experiment).all(),
    )


def main():
    # Start webserver
    app.logger.info("web running")
    app.run(host="0.0.0.0", port=8000, debug=True)
