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

        # Look up the experiment in database
        upload = (
            self.db.query(database.Upload)
            .options(joinedload(database.Upload.experiment))
            .get(upload_id)
        )
        if upload is None:
            self.set_status(404)
            return self.render('webcapture/notfound.html')

        return self.render(
            'webcapture/dashboard.html',
            filename=upload.filename,
            upload_short_id=upload.short_id,
        )


class StartRecord(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_start_record')
    async def post(self, upload_short_id):
        TODO
