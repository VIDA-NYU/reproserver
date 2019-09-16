from hashlib import sha256
import logging
import os
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import functions

from .. import database
from ..repositories import RepositoryError, get_experiment_from_repository, \
    parse_repository_url
from ..shortid import MultiShortIDs
from ..utils import secure_filename
from .base import BaseHandler


logger = logging.getLogger(__name__)


short_ids = MultiShortIDs(os.environ['SHORTIDS_SALT'])


class Index(BaseHandler):
    """Landing page from which a user can select an experiment to unpack.
    """
    def get(self):
        return self.render('index.html')


class Unpack(BaseHandler):
    """Target of the landing page.

    An experiment has been provided, store it and start the build process.
    """
    async def post(self):
        # If a URL was provided, and no file
        if self.get_body_argument('rpz_url', None):
            # Redirect to reproduce_repo view
            try:
                repo, repo_path = await parse_repository_url(
                    self.get_body_argument('rpz_url')
                )
            except RepositoryError as e:
                self.set_status(404)
                return self.render('repository_notfound.html', message=str(e))
            else:
                return self.redirect(self.reverse_url(
                    'reproduce_repo',
                    repo, repo_path,
                ))

        # Get uploaded file
        uploaded_file = self.request.files['rpz_file'][0]
        assert uploaded_file.filename
        logger.info("Incoming file: %r", uploaded_file.filename)
        filename = secure_filename(uploaded_file.filename)

        # Hash it
        hasher = sha256(uploaded_file.body)
        filehash = hasher.hexdigest()
        logger.info("Computed hash: %s", filehash)

        # Check for existence of experiment
        experiment = self.db.query(database.Experiment).get(filehash)
        if experiment:
            experiment.last_access = functions.now()
            logger.info("File exists in storage")
        else:
            # Insert it on S3
            await self.application.object_store.upload_bytes_async(
                'experiments',
                filehash,
                uploaded_file.body,
            )
            logger.info("Inserted file in storage")

            # Insert it in database
            experiment = database.Experiment(hash=filehash)
            self.db.add(experiment)

        # Insert Upload in database
        upload = database.Upload(experiment=experiment,
                                 filename=filename,
                                 submitted_ip=self.request.remote_ip)
        self.db.add(upload)
        self.db.commit()

        # Encode ID for permanent URL
        upload_short_id = upload.short_id

        # Redirect to build page
        return self.redirect(
            self.reverse_url('reproduce_local', upload_short_id),
            status=302,
        )


class BaseReproduce(BaseHandler):
    def reproduce(self, upload):
        experiment = upload.experiment
        filename = upload.filename
        experiment_url = self.url_for_upload(upload)
        try:
            # JSON endpoint, returns data for JavaScript to update the page
            if self.is_json_requested():
                log_from = int(self.get_query_argument('log_from', '0'), 10)
                return self.send_json({
                    'status': experiment.status.name,
                    'log': experiment.get_log(log_from),
                    'params': [
                        {'name': p.name, 'optional': p.optional,
                         'default': p.default}
                        for p in experiment.parameters]
                })
            # HTML view, return the page
            else:
                # If it's done building, send build log and run form
                if experiment.status == database.Status.BUILT:
                    logger.info("Experiment already built")
                    input_files = (
                        self.db.query(database.Path)
                        .filter(database.Path.experiment_hash ==
                                experiment.hash)
                        .filter(database.Path.is_input)).all()
                    return self.render(
                        'setup.html',
                        filename=filename,
                        built=True, error=False,
                        log=experiment.get_log(0),
                        params=experiment.parameters,
                        input_files=input_files,
                        upload_short_id=upload.short_id,
                        experiment_url=experiment_url,
                    )
                if experiment.status == database.Status.ERROR:
                    logger.info("Experiment is errored")
                    return self.render(
                        'setup.html',
                        filename=filename,
                        built=True, error=True,
                        log=experiment.get_log(0),
                        upload_short_id=upload.short_id,
                        experiment_url=experiment_url,
                    )
                # If it's currently building, show the log
                elif experiment.status == database.Status.BUILDING:
                    logger.info("Experiment is currently building")
                    return self.render(
                        'setup.html',
                        filename=filename,
                        built=False, log=experiment.get_log(0),
                        upload_short_id=upload.short_id,
                        experiment_url=experiment_url,
                    )
                # Else, trigger the build
                else:
                    if experiment.status == database.Status.NOBUILD:
                        logger.info("Triggering a build")
                        experiment.status = database.Status.QUEUED
                        self.application.builder.build(experiment.hash)
                    return self.render(
                        'setup.html',
                        filename=filename,
                        built=False,
                        upload_short_id=upload.short_id,
                        experiment_url=experiment_url,
                    )
        finally:
            self.db.commit()


class ReproduceRepo(BaseReproduce):
    async def get(self, repo, repo_path):
        """Reproduce an experiment from a data repository.
        """
        # Check the database for an experiment already stored matching the URI
        repository_key = '%s/%s' % (repo, repo_path)
        upload = (
            self.db.query(database.Upload)
            .options(joinedload(database.Upload.experiment))
            .filter(database.Upload.repository_key == repository_key)
            .order_by(database.Upload.id.desc())
        ).first()
        if upload is None:
            try:
                upload = await get_experiment_from_repository(
                    self.db, self.application.object_store,
                    self.request.remote_ip,
                    repo, repo_path,
                )
            except RepositoryError as e:
                self.set_status(404)
                return self.render('setup_notfound.html', message=str(e))

        # Also updates last access
        upload.experiment.last_access = functions.now()

        return self.reproduce(upload)


class ReproduceLocal(BaseReproduce):
    def get(self, upload_short_id):
        """Show build log and ask for run parameters.
        """
        # Decode info from URL
        logger.info("Decoding %r", upload_short_id)
        try:
            upload_id = short_ids.decode(b'upload', upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

        # Look up the experiment in database
        upload = (
            self.db.query(database.Upload)
            .options(joinedload(database.Upload.experiment))
            .get(upload_id)
        )
        if upload is None:
            self.set_status(404)
            return self.render('setup_notfound.html')

        # Also updates last access
        upload.experiment.last_access = functions.now()

        return self.reproduce(upload)


class StartRun(BaseHandler):
    async def post(self, upload_short_id):
        """Gets the run parameters POSTed to from /reproduce.

        Triggers the run and redirects to the results page.
        """
        # Decode info from URL
        logger.info("Decoding %r", upload_short_id)
        try:
            upload_id = short_ids.decode(b'upload', upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

        # Look up the experiment in database
        upload = (
            self.db.query(database.Upload)
            .options(joinedload(database.Upload.experiment))
            .get(upload_id)
        )
        if upload is None:
            self.set_status(404)
            return self.render('setup_notfound.html')
        experiment = upload.experiment

        # New run entry
        run = database.Run(experiment_hash=experiment.hash,
                           upload_id=upload_id)
        self.db.add(run)

        # Get list of parameters
        params = set()
        params_unset = set()
        for param in experiment.parameters:
            if not param.optional:
                params_unset.add(param.name)
            params.add(param.name)

        # Get run parameters
        for k, v in self.request.body_arguments.items():
            if k.startswith('param_'):
                if not v:
                    continue
                name = k[6:]
                if name not in params:
                    raise ValueError("Unknown parameter %s" % k)
                v = v[-1].decode('utf-8')
                run.parameter_values.append(
                    database.ParameterValue(name=name, value=v)
                )
                params_unset.discard(name)

        if params_unset:
            raise ValueError("Missing value for parameters: %s" %
                             ", ".join(params_unset))

        # Get list of input files
        input_files = set(
            p.name for p in (
                self.db.query(database.Path)
                .filter(database.Path.experiment_hash == experiment.hash)
                .filter(database.Path.is_input)
            ).all())

        # Get input files
        for k, uploaded_file in self.request.files.items():
            if not uploaded_file:
                continue
            uploaded_file = uploaded_file[0]

            if not k.startswith('inputfile_') or k[10:] not in input_files:
                raise ValueError("Unknown input file %s" % k)

            name = k[10:]
            logger.info("Incoming input file: %s", name)

            # Hash file
            hasher = sha256(uploaded_file.body)
            inputfilehash = hasher.hexdigest()
            logger.info("Computed hash: %s", inputfilehash)

            # Insert it into S3
            await self.application.object_store.upload_bytes_async(
                'inputs',
                inputfilehash,
                uploaded_file.body,
            )
            logger.info("Inserted file in storage")

            # Insert it in database
            input_file = database.InputFile(
                hash=inputfilehash, name=name,
                size=len(uploaded_file.body),
            )
            run.input_files.append(input_file)

        # Get ports to expose
        for port_str in self.get_body_argument('ports', '').split():
            port_str = port_str.strip()
            if port_str:
                try:
                    port = int(port_str)
                    if not (1 <= port <= 65535):
                        raise ValueError
                except (ValueError, OverflowError):
                    raise ValueError("Invalid port number %r" % port_str)
                run.ports.append(database.RunPort(
                    port_number=port,
                ))

        # Trigger run
        self.db.commit()
        self.application.runner.run(run.id)

        # Redirect to results page
        return self.redirect(
            self.reverse_url('results', run.short_id),
            status=302,
        )


class Results(BaseHandler):
    def get(self, run_short_id):
        """Shows the results of a run, whether it's done or in progress.
        """
        # Decode info from URL
        logger.info("Decoding %r", run_short_id)
        try:
            run_id = short_ids.decode(b'run', run_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

        # Look up the run in the database
        run = (
            self.db.query(database.Run)
            .options(joinedload(database.Run.experiment),
                     joinedload(database.Run.upload),
                     joinedload(database.Run.parameter_values),
                     joinedload(database.Run.input_files),
                     joinedload(database.Run.output_files))
        ).get(run_id)
        if run is None:
            self.set_status(404)
            return self.render('results_notfound.html')
        # Update last access
        run.experiment.last_access = functions.now()
        self.db.commit()

        # JSON endpoint, returns data for JavaScript to update the page
        if self.is_json_requested():
            log_from = int(self.get_query_argument('log_from', '0'), 10)
            return self.send_json({
                'started': bool(run.started),
                'done': bool(run.done),
                'log': run.get_log(log_from),
            })
        # HTML view, return the page
        else:
            def get_port_url(port_number):
                tpl = os.environ.get(
                    'WEB_PROXY_URL',
                    'http://{short_id}-{port}.127.0.0.1.xip.io:8001',
                )
                return tpl.format(
                    short_id=run_short_id,
                    port=port_number,
                )

            return self.render(
                'results.html',
                run=run,
                log=run.get_log(0),
                started=bool(run.started),
                done=bool(run.done),
                experiment_url=self.url_for_upload(run.upload),
                get_port_url=get_port_url,
            )


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
