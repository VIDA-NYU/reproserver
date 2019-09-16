import logging
import os
import tornado.ioloop

from .proxy import ProxyHandler
from .shortid import MultiShortIDs
from .web import make_app


logger = logging.getLogger(__name__)


short_ids = MultiShortIDs(os.environ['SHORTIDS_SALT'])


class ExternalProxyHandler(ProxyHandler):
    def select_destination(self):
        # Read destination from hostname
        self.original_host = self.request.host
        host_name = self.request.host_name.split('.', 1)[0]
        run_short_id, port = host_name.split('-')
        run_id = short_ids.decode(b'run', run_short_id)

        url = 'run-{0}:5597{1}'.format(run_id, self.request.uri)
        return url

    def alter_request(self, request):
        # Authentication
        request.headers['X-Reproserver-Authenticate'] = 'secret-token'

        request.headers['Host'] = self.original_host


def main():
    logging.root.handlers.clear()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")

    app = make_app()
    app.listen(8000, address='0.0.0.0', max_buffer_size=1_073_741_824)

    proxy = ExternalProxyHandler.make_app()
    proxy.listen(8001, address='0.0.0.0')

    loop = tornado.ioloop.IOLoop.current()
    print("\n    reproserver is now running: http://localhost:8000/\n")
    loop.start()
