import logging

from .base import BaseHandler
from .views import PROM_REQUESTS


logger = logging.getLogger(__name__)


class Index(BaseHandler):
    """Landing page to upload an RPZ.
    """
    @PROM_REQUESTS.sync('webcapture_index')
    def get(self):
        return self.render('webcapture_index.html')


class Upload(BaseHandler):
    """Upload RPZ.
    """
    @PROM_REQUESTS.async_('webcapture_upload')
    async def post(self):
        # TODO
        return self.redirect(self.reverse_url(
            'webcapture_status',
            upload_short_id,
        ))


class Status(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_status')
    def get(self, upload_short_id):
        return self.render('webcapture_status.html')


class Record(BaseHandler):
    @PROM_REQUESTS.sync('webcapture_record')
    def get(self):
        return self.render('webcapture_record.html')
