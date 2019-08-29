import logging
import os

from tornado import httputil
from tornado import httpclient
from tornado.routing import URLSpec
import tornado.web
from tornado.websocket import WebSocketHandler, websocket_connect

from . import database
from .shortid import MultiShortIDs


logger = logging.getLogger(__name__)


short_ids = MultiShortIDs(os.environ['SHORTIDS_SALT'])


# https://github.com/senko/tornado-proxy/blob/master/tornado_proxy/proxy.py


class Application(tornado.web.Application):
    def __init__(self, handlers, **kwargs):
        super(Application, self).__init__(handlers, **kwargs)

        #engine, self.DBSession = database.connect()


class ProxyHandler(WebSocketHandler):
    def __init__(self, application, request, **kwargs):
        super(ProxyHandler, self).__init__(application, request, **kwargs)
        self.headers = []
        #self.db = application.DBSession()

    #def get_run(self):
    #    # Decode the ID
    #    try:
    #        run_id = short_ids.decode('run', self.request.host_name)
    #    except ValueError:
    #        raise tornado.web.HTTPError(403)
#
    #    # Look up the run in the database
    #    run = (
    #        self.db.query(database.Run)
    #        # FIXME: joinedload here probably
    #    ).get(run_id)
    #    if not run:
    #        raise tornado.web.HTTPError(403)

    async def get(self):
        logger.info("Incoming connection, host=%r", self.request.host)

        # TODO: Check configuration for host, decide destination
        logger.info("%s %r", self.request.method, self.request.uri)
        host = 'localhost:8888'
        url = 'http://localhost:8888' + self.request.uri

        if self.request.headers.get('Upgrade', '').lower() == 'websocket':
            url = 'ws://' + url[7:]
            headers = dict(self.request.headers)
            headers['Host'] = host
            self.upstream_ws = await websocket_connect(
                httpclient.HTTPRequest(
                    url,
                    headers=headers,
                ),
                on_message_callback=self.on_upstream_message,
            )
            return await WebSocketHandler.get(self)
        else:
            headers = dict(self.request.headers)
            headers['Host'] = host
            await httpclient.AsyncHTTPClient().fetch(
                url,
                method=self.request.method,
                headers=headers,
                body=self.request.body or None,
                header_callback=self.got_header,
                streaming_callback=self.write,
                raise_error=False,
            )
            return self.finish()

    def post(self):
        return self.get()

    def got_header(self, header):
        if not self.headers:
            first_line = httputil.parse_response_start_line(header)
            self.set_status(first_line.code, first_line.reason)
            self.headers.append(header)
        elif header != '\r\n':
            self.headers.append(header)
        else:
            for line in self.headers[1:]:
                name, value = line.split(":", 1)
                self.set_header(name, value.strip())

    def on_ws_connection_close(self, close_code, close_reason):
        self.upstream_ws.close(close_code, close_reason)

    def on_message(self, message):
        return self.upstream_ws.write_message(message)

    def on_upstream_message(self, message):
        if message is None:
            self.close()
        else:
            return self.write_message(message, isinstance(message, bytes))


def make_proxy():
    return Application(
        [
            URLSpec('.*', ProxyHandler),
        ],
    )
