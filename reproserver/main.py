import asyncio
import logging
import os
import prometheus_client
import tornado.ioloop

from .web import make_app


logger = logging.getLogger(__name__)


def main():
    logging.root.handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    prometheus_client.start_http_server(8090)

    debug = os.environ.get('REPROSERVER_DEBUG', '').lower() in (
        'y', 'yes', 'true', 'on', '1',
    )
    if debug:
        logger.warning("Debug mode is ON")
        asyncio.get_event_loop().set_debug(True)
    app = make_app(debug)
    app.listen(8000, address='0.0.0.0',
               xheaders=True,
               max_buffer_size=1_073_741_824)

    loop = tornado.ioloop.IOLoop.current()
    print("\n    reproserver is now running: http://localhost:8000/\n")
    loop.start()
