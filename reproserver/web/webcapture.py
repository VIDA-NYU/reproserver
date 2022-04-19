import json
import logging
from sqlalchemy.orm import joinedload

from .base import BaseHandler
from .views import PROM_REQUESTS, store_uploaded_rpz
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
            return self.render(
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
            return self.render('webcapture/badfile.html', message=str(e))

        # Redirect to dashboard
        return self.redirect(
            self.reverse_url('webcapture_dashboard', upload_short_id),
            status=302,
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


class Record(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_record')
    async def post(self, upload_short_id):
        TODO


class StartCrawl(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_start_crawl')
    async def post(self, upload_short_id):
        TODO


class UploadWacz(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_upload_wacz')
    async def post(self, upload_short_id):
        TODO


class Download(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_download')
    async def post(self, upload_short_id):
        TODO
