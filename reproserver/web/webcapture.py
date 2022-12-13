import asyncio
from datetime import datetime
import json
import logging
import os
from hashlib import sha256
from reprozip_web.combine import combine
from sqlalchemy.orm import joinedload
import tempfile
from tornado.web import HTTPError
from tornado.websocket import WebSocketHandler, websocket_connect
from urllib.parse import urlencode

from .base import BaseHandler
from .views import PROM_REQUESTS
from ..utils import background_future
from .. import database


logger = logging.getLogger(__name__)


class Dashboard(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_dashboard')
    def get(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

        wacz_hash = self.get_query_argument('wacz', None)

        hostname = self.get_query_argument('hostname', 'localhost')
        port_number = self.get_query_argument('port_number', '3000')
        try:
            port_number = int(port_number, 10)
            if not (1 <= port_number <= 65535):
                raise OverflowError
        except (ValueError, OverflowError):
            raise HTTPError(400, "Wrong port number")

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
            return self.render('setup_notfound.html')

        if wacz_hash:
            try:
                meta = self.application.object_store.get_file_metadata(
                    'web1',
                    wacz_hash + '.wacz',
                )
            except KeyError:
                self.set_status(404)
                return self.render('setup_notfound.html')

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
            experiment_url=self.url_for_upload(upload),
            upload_short_id=upload.short_id,
            wacz=wacz,
            hostname=hostname,
            port_number=port_number,
        )


class Preview(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_preview')
    def post(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

        wacz_hash = self.get_query_argument('wacz')

        self.get_body_argument('hostname')
        port_number = self.get_body_argument('port_number')
        try:
            port_number = int(port_number, 10)
            if not (1 <= port_number <= 65535):
                raise OverflowError
        except (ValueError, OverflowError):
            raise HTTPError(400, "Wrong port number")

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
                           upload_id=upload_id,
                           submitted_ip=self.request.remote_ip)
        self.db.add(run)

        # Expose port
        run.ports.append(database.RunPort(
            port_number=port_number,
        ))

        # Trigger run
        self.db.commit()
        background_future(self.application.runner.run(run.id))

        # Redirects to crawl status page
        return self.redirect(
            self.reverse_url('results', run.short_id, wacz=wacz_hash),
            status=303,
        )


class StartRecord(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_start_record')
    def post(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

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
                           upload_id=upload_id,
                           submitted_ip=self.request.remote_ip)
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
                self.reverse_url(
                    'webcapture_record',
                    upload_short_id,
                    run.short_id,
                ) + '#' + urlencode({'url': seed_url})
            ),
            status=303,
        )


class Record(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_record')
    def get(self, upload_short_id, run_short_id):
        # Decode info from URL
        try:
            run_id = database.Run.decode_id(run_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

        # Look up the run in the database
        run = (
            self.db.query(database.Run)
            .options(
                joinedload(database.Run.upload),
            )
        ).get(run_id)
        if run is None or run.upload.short_id != upload_short_id:
            self.set_status(404)
            return self.render('setup_notfound.html')

        # Get the port number
        if len(run.ports) != 1:
            logger.warning(
                "Run has %d ports, can't load into record view",
                len(run.ports),
            )
            return self.render('setup_notfound.html')
        port_number = run.ports[0].port_number

        return self.render(
            'webcapture/record.html',
            run=run,
            upload_short_id=upload_short_id,
            experiment_url=self.url_for_upload(run.upload),
            log=run.get_log(0),
            port_number=port_number,
        )


BROWSERTRIX_SCRIPT = r'''
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
if ! crawl \
    --url "__URL__" \
    --screencastPort 9223 \
    --workers 2 \
    --generateWACZ
then
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
    -F "hostname=__HOSTNAME__" \
    -F "port_number=__PORT_NUMBER__" \
    http://web:8000/web/__UPLOAD_SHORT_ID__/upload-wacz?run=__RUN_ID__)"
if [ "$CURL_STATUS" != 303 ]; then
    printf "upload failed (status %s)\n" "$CURL_STATUS" >&2
    exit 1
fi
'''


class StartCrawl(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_start_crawl')
    def post(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

        hostname = self.get_body_argument('hostname')
        port_number = self.get_body_argument('port_number')
        try:
            port_number = int(port_number, 10)
            if not (1 <= port_number <= 65535):
                raise OverflowError
        except (ValueError, OverflowError):
            raise HTTPError(400, "Wrong port number")

        if hostname != 'localhost':
            logger.warning("Using 'localhost' instead of '%s'", hostname)
            hostname = 'localhost'

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
                           upload_id=upload_id,
                           submitted_ip=self.request.remote_ip)
        self.db.add(run)

        self.db.flush()  # Set run.id

        # Mark exposed port
        run.ports.append(database.RunPort(
            port_number=port_number,
        ))

        # Add browsertrix container
        script = BROWSERTRIX_SCRIPT
        for k, v in {
            '__URL__': seed_url,
            '__UPLOAD_SHORT_ID__': upload_short_id,
            '__RUN_ID__': run.short_id,
            '__HOSTNAME__': hostname,
            '__PORT_NUMBER__': str(port_number),
        }.items():
            script = script.replace(k, v)
        run.extra_config = json.dumps({
            'required': {
                'containers': [
                    {
                        'name': 'browsertrix',
                        'image': os.environ['BROWSERTRIX_IMAGE'],
                        'args': ['sh', '-c', script],
                    },
                ],
                'ports': [
                    {
                        'name': 'browsertrix',
                        'protocol': 'TCP',
                        'port': 9223,
                    },
                ],
            },
        })

        # Trigger run
        self.db.commit()
        background_future(self.application.runner.run(run.id))

        # Redirects to crawl status page
        return self.redirect(
            self.reverse_url(
                'webcapture_crawl_status',
                upload_short_id,
                run.short_id,
            ),
            status=303,
        )


class CrawlStatus(BaseHandler):
    def get(self, upload_short_id, run_short_id):
        # Decode info from URL
        try:
            run_id = database.Run.decode_id(run_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.render('setup_notfound.html')

        # Look up the run in the database
        run = (
            self.db.query(database.Run)
            .options(
                joinedload(database.Run.upload),
            )
        ).get(run_id)
        if run is None or run.upload_id != upload_id:
            self.set_status(404)
            return self.render('setup_notfound.html')

        # Look for an output WACZ in the database
        wacz = None
        hostname = None
        port_number = None
        extension_result = (
            self.db.query(database.RunExtensionResult)
        ).get(dict(run_id=run_id, extension_name='web1', name='wacz'))
        if extension_result:
            extension_result = json.loads(extension_result.value)
            wacz = extension_result['wacz_hash']
            hostname = extension_result['hostname']
            port_number = extension_result['port_number']

        return self.render(
            'webcapture/crawl_results.html',
            run=run,
            experiment_url=self.url_for_upload(run.upload),
            log=run.get_log(0),
            wacz=wacz,
            hostname=hostname,
            port_number=port_number,
        )


class CrawlStatusWebsocket(WebSocketHandler, BaseHandler):
    def get(self, upload_short_id, run_short_id):
        # Decode info from URL
        try:
            run_id = database.Run.decode_id(run_short_id)
        except ValueError:
            self.set_status(404)
            return self.finish("Not found")
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return self.finish("Not found")

        # Look up the run in the database
        self.run = (
            self.db.query(database.Run)
        ).get(run_id)
        if self.run is None or self.run.upload_id != upload_id:
            self.set_status(404)
            return self.finish("Not found")

        return super(CrawlStatusWebsocket, self).get(
            upload_short_id,
            run_short_id,
        )

    async def open(self, upload_short_id, run_short_id):
        self.upstream_ws = await websocket_connect(
            'ws://run-%d:9223/ws' % self.run.id,
            on_message_callback=self.on_upstream_message,
        )

    def on_message(self, message):
        return self.upstream_ws.write_message(message)

    def on_upstream_message(self, message):
        if message is None:
            self.close()
        else:
            return self.write_message(message, isinstance(message, bytes))

    def on_ws_connection_close(self, close_code=None, close_reason=None):
        self.upstream_ws.close(close_code, close_reason)


class UploadWacz(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_upload_wacz')
    async def get(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return await self.render('setup_notfound.html')
        hostname = self.get_query_argument('hostname')
        port_number = self.get_query_argument('port_number')
        try:
            port_number = int(port_number, 10)
            if not (1 <= port_number <= 65535):
                raise OverflowError
        except (ValueError, OverflowError):
            raise HTTPError(400, "Wrong port number")

        upload = (
            self.db.query(database.Upload)
            .get(upload_id)
        )
        if upload is None:
            self.set_status(404)
            return await self.render('setup_notfound.html')

        return await self.render(
            'webcapture/upload_wacz.html',
            upload=upload,
            experiment_url=self.url_for_upload(upload),
            hostname=hostname,
            port_number=port_number,
        )

    @PROM_REQUESTS.sync('webcapture_upload_wacz')
    async def post(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return await self.render('setup_notfound.html')
        hostname = self.get_body_argument('hostname')
        port_number = self.get_body_argument('port_number')
        try:
            port_number = int(port_number, 10)
            if not (1 <= port_number <= 65535):
                raise OverflowError
        except (ValueError, OverflowError):
            raise HTTPError(400, "Wrong port number")

        upload = (
            self.db.query(database.Upload)
            .get(upload_id)
        )
        if upload is None:
            self.set_status(404)
            return await self.render('setup_notfound.html')

        try:
            uploaded_file = self.request.files['wacz_file'][0]
        except (KeyError, IndexError):
            return await self.render(
                'webcapture/upload_wacz_bad.html',
                upload=upload,
                experiment_url=self.url_for_upload(upload),
            )

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

        # Store which run it came from, if provided
        run_short_id = self.get_query_argument('run', None)
        if run_short_id is not None:
            try:
                run_id = database.Run.decode_id(run_short_id)
            except ValueError:
                self.set_status(404)
                return self.render('setup_notfound.html')
            logger.info("Associating WACZ with run %d", run_id)
            self.db.add(database.RunExtensionResult(
                run_id=run_id,
                extension_name='web1',
                name='wacz',
                value=json.dumps({
                    'wacz_hash': wacz_hash,
                    'hostname': hostname,
                    'port_number': port_number,
                }),
            ))
            self.db.commit()

        redirect_url = self.reverse_url(
            'webcapture_dashboard',
            upload_short_id,
            wacz=wacz_hash,
            hostname=hostname,
            port_number=port_number,
        )

        # Send JSON response if Accept: application/json (for use with fetch)
        if self.is_json_requested():
            return self.send_json({"redirect_url": redirect_url})
        else:
            return self.redirect(redirect_url, status=303)

    def check_xsrf_cookie(self):
        # Disable XSRF prevention here, to allow upload from browsertrix
        pass


class Download(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_download')
    async def post(self, upload_short_id):
        # Decode info from URL
        try:
            upload_id = database.Upload.decode_id(upload_short_id)
        except ValueError:
            self.set_status(404)
            return await self.render('setup_notfound.html')

        wacz_hash = self.get_query_argument('wacz')

        # TODO: Those go into the RPZ file somewhere
        self.get_body_argument('hostname')
        port_number = self.get_body_argument('port_number')
        try:
            port_number = int(port_number, 10)
            if not (1 <= port_number <= 65535):
                raise OverflowError
        except (ValueError, OverflowError):
            raise HTTPError(400, "Wrong port number")

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
                return await self.render('setup_notfound.html')
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
