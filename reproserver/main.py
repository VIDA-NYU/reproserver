import logging
import tornado.ioloop

from .web import make_app


logger = logging.getLogger(__name__)


def main():
    logging.root.handlers.clear()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")

    app = make_app()
    app.listen(8000, address='0.0.0.0')
    loop = tornado.ioloop.IOLoop.current()

    print("\n    reproserver is now running: http://localhost:8000/\n")
    loop.start()
