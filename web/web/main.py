from common import database, TaskQueues, get_object_store
from common.shortid import MultiShortIDs
import flask
from flask import Flask, jsonify, redirect, render_template, request, url_for
import functools
from hashlib import sha256
import logging
import mimetypes
import os
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import functions
from werkzeug.contrib.fixers import ProxyFix
from werkzeug.utils import secure_filename

from web.providers import ProviderError, get_experiment_from_provider


app = Flask(__name__)


short_ids = MultiShortIDs(os.environ['SHORTIDS_SALT'])


# Middleware allowing this to be run behind a reverse proxy
if 'WEB_BEHIND_PROXY' in os.environ:
    # Use ProxyFix to fix the remote address, HTTP host and HTTP scheme
    app.wsgi_app = ProxyFix(app.wsgi_app)

    # Fix SCRIPT_NAME to allow the app to run under a subdirectory
    old_app = app.wsgi_app

    def wsgi_app(environ, start_response):
        script_name = environ.get('HTTP_X_SCRIPT_NAME', '')
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ['PATH_INFO']
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]

        scheme = environ.get('HTTP_X_SCHEME', '')
        if scheme:
            environ['wsgi.url_scheme'] = scheme
        return old_app(environ, start_response)

    app.wsgi_app = wsgi_app


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

object_store.create_buckets()


def sql_session(func):
    def wrapper(**kwargs):
        session = SQLSession()
        flask.g.sql_session = session
        try:
            return func(session=session, **kwargs)
        finally:
            flask.g.sql_session = None
            session.close()
    functools.update_wrapper(wrapper, func)
    return wrapper


def url_for_upload(upload):
    if upload.provider_key is not None:
        provider, path = upload.provider_key.split('/', 1)
        return url_for('reproduce_provider',
                       provider=provider, provider_path=path)
    else:
        return url_for('reproduce_local', upload_short_id=upload.short_id)


@app.context_processor
def context():
    def output_link(output_file):
        client_endpoint_url = os.environ.get('S3_CLIENT_URL')
        if client_endpoint_url:
            client = get_object_store(client_endpoint_url)
        else:
            client = object_store
        session = flask.g.sql_session
        path = session.query(database.Path).filter(
            database.Path.experiment_hash == output_file.run.experiment_hash,
            database.Path.name == output_file.name).one().path
        mime = mimetypes.guess_type(path)[0]
        return client.presigned_serve_url('outputs', output_file.hash,
                                          output_file.name,
                                          mime)

    return dict(output_link=output_link,
                url_for_upload=url_for_upload,
                version=os.environ.get('REPROSERVER_VERSION', 'dev'))


@app.route('/')
@sql_session
def index(session):
    """Landing page from which a user can select an experiment to unpack.
    """
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
@sql_session
def unpack(session):
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
    experiment = session.query(database.Experiment).get(filehash)
    if experiment:
        experiment.last_access = functions.now()
        app.logger.info("File exists in storage")
    else:
        # Insert it on S3
        object_store.upload_fileobj('experiments', filehash, uploaded_file)
        app.logger.info("Inserted file in storage")

        # Insert it in database
        experiment = database.Experiment(hash=filehash)
        session.add(experiment)

    # Insert Upload in database
    upload = database.Upload(experiment=experiment,
                             filename=filename,
                             submitted_ip=request.remote_addr)
    session.add(upload)
    session.commit()

    # Encode ID for permanent URL
    upload_short_id = upload.short_id

    # Redirect to build page
    return redirect(url_for('reproduce_local',
                            upload_short_id=upload_short_id), 302)


@app.route('/reproduce/<string:provider>/<path:provider_path>')
@sql_session
def reproduce_provider(provider, provider_path, session):
    """Reproduce an experiment from a data repository (provider).
    """
    # Check the database for an experiment already stored matching the URI
    provider_key = '%s/%s' % (provider, provider_path)
    upload = (session.query(database.Upload)
              .options(joinedload(database.Upload.experiment))
              .filter(database.Upload.provider_key == provider_key)
              .order_by(database.Upload.id.desc())).first()
    if not upload:
        try:
            upload = get_experiment_from_provider(session, request.remote_addr,
                                                  provider, provider_path)
        except ProviderError as e:
            return render_template('setup_notfound.html',
                                   message=e.message), 404

    # Also updates last access
    upload.experiment.last_access = functions.now()

    return reproduce_common(upload, session)


@app.route('/reproduce/<upload_short_id>')
@sql_session
def reproduce_local(upload_short_id, session):
    """Show build log and ask for run parameters.
    """
    # Decode info from URL
    app.logger.info("Decoding %r", upload_short_id)
    try:
        upload_id = short_ids.decode('upload', upload_short_id)
    except ValueError:
        return render_template('setup_notfound.html'), 404

    # Look up the experiment in database
    upload = (session.query(database.Upload)
              .options(joinedload(database.Upload.experiment))
              .get(upload_id))
    if not upload:
        return render_template('setup_notfound.html'), 404

    # Also updates last access
    upload.experiment.last_access = functions.now()

    return reproduce_common(upload, session)


def reproduce_common(upload, session):
    experiment = upload.experiment
    filename = upload.filename
    experiment_url = url_for_upload(upload)
    try:
        # JSON endpoint, returns data for JavaScript to update the page
        if (request.accept_mimetypes.best_match(['application/json',
                                                 'text/html']) ==
                'application/json'):
            log_from = int(request.args.get('log_from', '0'), 10)
            return jsonify({'status': experiment.status.name,
                            'log': experiment.get_log(log_from),
                            'params': [
                                {'name': p.name, 'optional': p.optional,
                                 'default': p.default}
                                for p in experiment.parameters]})
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
                                       upload_short_id=upload.short_id,
                                       experiment_url=experiment_url)
            if experiment.status == database.Status.ERROR:
                app.logger.info("Experiment is errored")
                return render_template('setup.html', filename=filename,
                                       built=True, error=True,
                                       log=experiment.get_log(0),
                                       upload_short_id=upload.short_id,
                                       experiment_url=experiment_url)
            # If it's currently building, show the log
            elif experiment.status == database.Status.BUILDING:
                app.logger.info("Experiment is currently building")
                return render_template('setup.html', filename=filename,
                                       built=False, log=experiment.get_log(0),
                                       upload_short_id=upload.short_id,
                                       experiment_url=experiment_url)
            # Else, trigger the build
            else:
                if experiment.status == database.Status.NOBUILD:
                    app.logger.info("Triggering a build, sending message")
                    experiment.status = database.Status.QUEUED
                    tasks.publish_build_task(experiment.hash)
                return render_template('setup.html', filename=filename,
                                       built=False,
                                       upload_short_id=upload.short_id,
                                       experiment_url=experiment_url)
    finally:
        session.commit()


@app.route('/run/<upload_short_id>', methods=['POST'])
@sql_session
def start_run(upload_short_id, session):
    """Gets the run parameters POSTed to from /reproduce.

    Triggers the run and redirects to the results page.
    """
    # Decode info from URL
    app.logger.info("Decoding %r", upload_short_id)
    try:
        upload_id = short_ids.decode('upload', upload_short_id)
    except ValueError:
        return render_template('setup_notfound.html'), 404

    # Look up the experiment in database
    upload = (session.query(database.Upload)
              .options(joinedload(database.Upload.experiment))
              .get(upload_id))
    if not upload:
        return render_template('setup_notfound.html'), 404
    experiment = upload.experiment

    # New run entry
    try:
        run = database.Run(experiment_hash=experiment.hash,
                           upload_id=upload_id)
        session.add(run)

        # Get list of parameters
        params = set()
        params_unset = set()
        for param in experiment.parameters:
            if not param.optional:
                params_unset.add(param.name)
            params.add(param.name)

        # Get run parameters
        for k, v in request.form.items():
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
        input_files = set(
            p.name for p in (
                session.query(database.Path)
                .filter(database.Path.experiment_hash == experiment.hash)
                .filter(database.Path.is_input).all()))

        # Get input files
        for k, uploaded_file in request.files.items():
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
            object_store.upload_fileobj('inputs', inputfilehash, uploaded_file)
            app.logger.info("Inserted file in storage")

            # Insert it in database
            input_file = database.InputFile(hash=inputfilehash, name=name,
                                            size=filesize)
            run.input_files.append(input_file)

        # Trigger run
        session.commit()
        tasks.publish_run_task(str(run.id))

        # Redirect to results page
        return redirect(url_for('results', run_short_id=run.short_id), 302)
    except Exception:
        session.rollback()
        raise


@app.route('/results/<run_short_id>')
@sql_session
def results(run_short_id, session):
    """Shows the results of a run, whether it's done or in progress.
    """
    # Decode info from URL
    app.logger.info("Decoding %r", run_short_id)
    try:
        run_id = short_ids.decode('run', run_short_id)
    except ValueError:
        return render_template('setup_notfound.html'), 404

    # Look up the run in the database
    run = (session.query(database.Run)
           .options(joinedload(database.Run.experiment),
                    joinedload(database.Run.upload),
                    joinedload(database.Run.parameter_values),
                    joinedload(database.Run.input_files),
                    joinedload(database.Run.output_files))
           .get(run_id))
    if not run:
        return render_template('results_notfound.html'), 404
    # Update last access
    run.experiment.last_access = functions.now()
    session.commit()

    # JSON endpoint, returns data for JavaScript to update the page
    if (request.accept_mimetypes.best_match(['application/json',
                                             'text/html']) ==
            'application/json'):
        log_from = int(request.args.get('log_from', '0'), 10)
        return jsonify({'started': bool(run.started),
                        'done': bool(run.done),
                        'log': run.get_log(log_from)})
    # HTML view, return the page
    else:
        return render_template('results.html', run=run,
                               log=run.get_log(0),
                               started=bool(run.started),
                               done=bool(run.done),
                               experiment_url=url_for_upload(run.upload))


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/data')
@sql_session
def data(session):
    """Print some system information.
    """
    return render_template(
        'data.html',
        experiments=session.query(database.Experiment).all(),
    )


@app.route('/healthz')
def health():
    """For Kubernetes liveness probe.
    """
    return ''


def main():
    # Start webserver
    app.logger.info("web running")
    app.run(host="0.0.0.0", port=8000, debug=True)
