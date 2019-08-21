from common import TaskQueues, get_object_store
from hashlib import sha256
import logging
import os
from sqlalchemy.sql import functions

from web.providers import ProviderError, get_experiment_from_provider

from .. import database
from ..shortid import MultiShortIDs
from .base import BaseHandler


logger = logging.getLogger(__name__)


short_ids = MultiShortIDs(os.environ['SHORTIDS_SALT'])


# AMQP
tasks = TaskQueues()


# Object storage
object_store = get_object_store()

object_store.create_buckets()


class Index(BaseHandler):
    """Landing page from which a user can select an experiment to unpack.
    """
    def get(self):
        return self.render('index.html')


class Unpack(BaseHandler):
    """Target of the landing page.

    An experiment has been provided, store it and start the build process.
    """
    def post(self):
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


class BaseReproduce(BaseHandler):
    def reproduce(self, upload):
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


class ReproduceProvider(BaseReproduce):
    def get(self, provider, provider_path):
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


class ReproduceLocal(BaseReproduce):
    def get(self, upload_id):
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


class StartRun(BaseHandler):
    def post(self, upload_id):
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


class Results(BaseHandler):
    def get(self, run_id):
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


class About(BaseHandler):
    def get(self):
        return self.render('about.html')


class Data(BaseHandler):
    """Print some system information.
    """
    def get(self):
        return self.render(
            'data.html',
            experiments=self.db.query(database.Experiment).all(),
        )


class Health(BaseHandler):
    def get(self):
        return self.finish('ok')
