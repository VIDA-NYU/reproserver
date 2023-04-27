import asyncio
import importlib
import logging
import os
import tornado.ioloop

from .utils import setup
from .web import make_app


logger = logging.getLogger(__name__)


def main():
    setup()

    debug = os.environ.get('REPROSERVER_DEBUG', '').lower() in (
        'y', 'yes', 'true', 'on', '1',
    )
    if debug:
        logger.warning("Debug mode is ON")
        asyncio.get_event_loop().set_debug(True)
    proxy_env = os.environ.get('WEB_PROXY_CLASS', '')
    if proxy_env:
        logger.info("Enabling proxy using %s", proxy_env)
        module, klass = proxy_env.split(':', 1)
        module = importlib.import_module(module)
        proxy = getattr(module, klass)
    else:
        proxy = None
    app = make_app(debug, proxy=proxy)
    app.listen(8000, address='0.0.0.0', xheaders=True)

    loop = tornado.ioloop.IOLoop.current()
    print("\n    reproserver is now running: http://localhost:8000/\n")
    loop.start()
