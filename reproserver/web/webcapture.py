import asyncio
from datetime import datetime
import json
import logging
import os
from hashlib import sha256
from reprozip_web.combine import combine
from sqlalchemy.orm import joinedload
import tempfile
import textwrap
from tornado.web import HTTPError
from urllib.parse import urlencode

from .base import BaseHandler
from .views import PROM_REQUESTS, store_uploaded_rpz
from ..utils import background_future
from .. import rpz_metadata, database


logger = logging.getLogger(__name__)


class Index(BaseHandler):
    """Landing page to upload an RPZ.
    """
    @PROM_REQUESTS.sync('webcapture_index')
    def get(self):
        return self.render('webcapture/index.html')


class Upload(BaseHandler):
    """Upload RPZ.
    """
    @PROM_REQUESTS.async_('webcapture_upload')
    async def post(self):
        # Get uploaded file
        # FIXME: Don't hold the file in memory!
        try:
            uploaded_file = self.request.files['rpz_file'][0]
        except (KeyError, IndexError):
            return await self.render(
                'webcapture/badfile.html',
                message="Missing file",
            )

        try:
            upload_short_id = await store_uploaded_rpz(
                self.application.object_store,
                self.db,
                uploaded_file,
                self.request.remote_ip,
            )
        except rpz_metadata.InvalidPackage as e:
            return await self.render('webcapture/badfile.html', message=str(e))

        # Redirect to dashboard
        return self.redirect(
            self.reverse_url('webcapture_dashboard', upload_short_id),
            status=303,
        )


class Dashboard(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_dashboard')
    def get(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('webcapture/notfound.html')

        wacz_hash = self.get_query_argument('wacz', None)

        # Look up the experiment in database
        upload = (
            self.db.query(database.Upload)
            .options(
                joinedload(database.Upload.experiment).joinedload(
                    database.Experiment.extensions,
                )
            )
            .get(upload_id)
        )
        if upload is None:
            self.set_status(404)
            return self.render('webcapture/notfound.html')

        if wacz_hash:
            try:
                meta = self.application.object_store.get_file_metadata(
                    'web1',
                    wacz_hash + '.wacz',
                )
            except KeyError:
                self.set_status(404)
                return self.render('webcapture/notfound.html')

            wacz = {
                'hash': wacz_hash,
                'filesize': meta['size'],
                'url': self.application.object_store.presigned_serve_url(
                    'web1',
                    wacz_hash + '.wacz',
                    'archive.wacz',
                    'application/zip',
                )
            }
        else:
            # Look for web extension
            extensions = {
                extension.name: json.loads(extension.data)
                for extension in upload.experiment.extensions
            }
            if 'web1' in extensions:
                wacz_hash = extensions['web1']['filehash']
                return self.redirect(
                    self.reverse_url(
                        'webcapture_dashboard',
                        upload_short_id,
                        wacz=wacz_hash,
                    ),
                )

            wacz = None

        return self.render(
            'webcapture/dashboard.html',
            filename=upload.filename,
            upload_short_id=upload.short_id,
            wacz=wacz,
        )


class StartRecord(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_start_record')
    def post(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('webcapture/notfound.html')

        hostname = self.get_body_argument('hostname')
        port_number = self.get_body_argument('port_number')
        try:
            port_number = int(port_number, 10)
            if not (1 <= port_number <= 65535):
                raise OverflowError
        except (ValueError, OverflowError):
            raise HTTPError(400, "Wrong port number")

        if port_number == 80:
            seed_url = f'http://{hostname}/'
        else:
            seed_url = f'http://{hostname}:{port_number}/'

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

        # Update last access
        upload.last_access = datetime.utcnow()
        upload.experiment.last_access = datetime.utcnow()

        # New run entry
        run = database.Run(experiment_hash=experiment.hash,
                           upload_id=upload_id)
        self.db.add(run)

        # Mark exposed port
        run.ports.append(database.RunPort(
            port_number=port_number,
        ))

        # Trigger run
        self.db.commit()
        background_future(self.application.runner.run(run.id))

        # Redirects to recording page
        return self.redirect(
            (
                self.reverse_url('webcapture_record', run.short_id)
                + '#' + urlencode({'url': seed_url})
            ),
            status=303,
        )


class Record(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_record')
    def get(self, run_short_id):
        # Decode info from URL
        try:
            run_id = database.Run.decode_id(run_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('results_notfound.html')

        # Look up the run in the database
        run = (
            self.db.query(database.Run)
            .options(
                joinedload(database.Run.upload),
            )
        ).get(run_id)
        if run is None:
            self.set_status(404)
            return self.render('webcapture/notfound.html')

        # Get the port number
        if len(run.ports) != 1:
            logger.warning(
                "Run has %d ports, can't load into record view",
                len(run.ports),
            )
            return self.render('webcapture/notfound.html')
        port_number = run.ports[0].port_number

        return self.render(
            'webcapture/record.html',
            run=run,
            log=run.get_log(0),
            port_number=port_number,
        )


class StartCrawl(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_start_crawl')
    def post(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('webcapture/notfound.html')

        hostname = self.get_body_argument('hostname')
        port_number = self.get_body_argument('port_number')
        try:
            port_number = int(port_number, 10)
            if not (1 <= port_number <= 65535):
                raise OverflowError
        except (ValueError, OverflowError):
            raise HTTPError(400, "Wrong port number")

        if port_number == 80:
            seed_url = f'http://{hostname}/'
        else:
            seed_url = f'http://{hostname}:{port_number}/'

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

        # Update last access
        upload.last_access = datetime.utcnow()
        upload.experiment.last_access = datetime.utcnow()

        # New run entry
        run = database.Run(experiment_hash=experiment.hash,
                           upload_id=upload_id)
        self.db.add(run)

        # Mark exposed port
        run.ports.append(database.RunPort(
            port_number=port_number,
        ))

        # Add browsertrix container
        script = textwrap.dedent(r'''
        SLEPT=0
        CURL_STATUS="$(curl -s -o /dev/null -w "%{http_code}" \
            --connect-timeout 5 "__URL))")"
        while ! printf '%s\n' "$CURL_STATUS" | grep '^[23]' > /dev/null; do
            printf "waiting for web server (curl: $CURL_STATUS)\n" >&2
            sleep 5
            SLEPT=$((SLEPT + 5))
            if [ $SLEPT -gt 300 ]; then
                printf "web server didn't come online\n" >&2
                exit 1
            fi
            CURL_STATUS="$(curl -s -o /dev/null -w "%{http_code}" \
                --connect-timeout 5 "__URL__")"
        done
        printf 'web server ready (curl: %s)\n' "$CURL_STATUS"
        if ! crawl --url "__URL__" --screencastPort 9223 --workers 2 --generateWACZ; then
            printf "crawl failed\n" >&2
            exit 1
        fi
        WACZ_PATH=$(ls -1 /crawls/collections/*/*.wacz | head -n 1)
        if [ ! -e "$WACZ_PATH" ]; then
            printf "can't find WACZ\n" >&2
            exit 1
        fi
        ls -l $WACZ_PATH
        CURL_STATUS="$(curl -s -o /dev/null \
            -w "%{http_code}" \
            -F "wacz_file=@$WACZ_PATH" \
            http://web:8000/web/upload-wacz/__UPLOAD_SHORT_ID__)"
        if [ "$CURL_STATUS" != 303 ]; then
            printf "upload failed (status %s)\n" "$CURL_STATUS" >&2
            exit 1
        fi
        '''.replace(
            '__URL__', seed_url,
        ).replace(
            '__UPLOAD_SHORT_ID__', upload_short_id,
        ))
        run.extra_config = json.dumps({
            'required': {
                'containers': [
                    {
                        'name': 'browsertrix',
                        'image': os.environ['BROWSERTRIX_IMAGE'],
                        'args': ['sh', '-c', script],
                    },
                ],
            },
        })

        # Trigger run
        self.db.commit()
        background_future(self.application.runner.run(run.id))

        # Redirects to crawl status page
        return self.redirect(
            self.reverse_url('webcapture_crawl_status', run.short_id),
            status=303,
        )


class CrawlStatus(BaseHandler):
    def get(self, upload_short_id):
        # TODO: If finished, redirect to finished page
        return self.finish('TODO: Show crawl status and screencast')


class UploadWacz(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_upload_wacz')
    async def get(self, upload_short_id):
        return await self.render(
            'webcapture/upload_wacz.html',
            upload_short_id=upload_short_id,
        )

    @PROM_REQUESTS.sync('webcapture_upload_wacz')
    async def post(self, upload_short_id):
        try:
            uploaded_file = self.request.files['wacz_file'][0]
        except (KeyError, IndexError):
            return await self.render('webcapture/upload_wacz_bad.html')

        logger.info(
            "Incoming WACZ: %r %d bytes",
            uploaded_file.filename,
            len(uploaded_file.body),
        )

        # Hash file
        wacz_hash = sha256(uploaded_file.body).hexdigest()

        object_store = self.application.object_store
        try:
            object_store.get_file_metadata('web1', wacz_hash + '.wacz')
        except KeyError:
            # Insert it on S3
            await object_store.upload_bytes_async(
                'web1',
                wacz_hash + '.wacz',
                uploaded_file.body,
            )
            logger.info("WACZ uploaded to S3")
        else:
            logger.info("WACZ is already on S3")

        return self.redirect(
            self.reverse_url(
                'webcapture_dashboard',
                upload_short_id,
                wacz=wacz_hash,
            ),
            status=303,
        )

    def check_xsrf_cookie(self):
        # Disable XSRF prevention here, to allow upload from browsertrix
        pass


class Download(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_download')
    async def get(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return await self.render('webcapture/notfound.html')

        wacz_hash = self.get_query_argument('wacz')

        with tempfile.TemporaryDirectory() as directory:
            input_rpz = os.path.join(directory, 'input.rpz')
            input_wacz = os.path.join(directory, 'input.wacz')
            output_rpz = os.path.join(directory, 'output.rpz')

            # Download RPZ
            upload = (
                self.db.query(database.Upload)
                .get(upload_id)
            )
            if upload is None:
                self.set_status(404)
                return await self.render('webcapture/notfound.html')
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.application.object_store.download_file(
                    'experiments', upload.experiment_hash, input_rpz,
                ),
            )

            # Download WACZ
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.application.object_store.download_file(
                    'web1', wacz_hash + '.wacz', input_wacz,
                ),
            )

            # Build combined file
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: combine(input_rpz, input_wacz, output_rpz),
            )

            # Send combined file
            with open(output_rpz, 'rb') as fp:
                self.set_header('Content-Type', 'application/octet-stream')
                self.set_header(
                    'Content-Disposition',
                    'attachment; filename=results.web.rpz',
                )
                chunk = fp.read(1_000_000)
                while chunk:
                    self.write(chunk)
                    await self.flush()
                    if len(chunk) != 1_000_000:
                        break
                    chunk = fp.read(1_000_000)
            return await self.finish()
