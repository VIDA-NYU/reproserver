import logging
from tornado import httputil
from tornado import httpclient
from tornado.routing import URLSpec
import tornado.web
from tornado.websocket import WebSocketHandler, websocket_connect

from . import __version__


logger = logging.getLogger(__name__)


class ProxyHandler(WebSocketHandler):
    def __init__(self, application, request, **kwargs):
        super(ProxyHandler, self).__init__(application, request, **kwargs)
        self.headers = []

    def set_default_headers(self):
        self.set_header('Server', 'ReproServer/%s' % __version__)

    def select_destination(self):
        raise NotImplementedError

    def alter_request(self, request):
        pass

    async def get(self):
        logger.info("Incoming connection, host=%r", self.request.host)

        url = self.select_destination()
        if self._finished:
            return

        if self.request.headers.get('Upgrade', '').lower() == 'websocket':
            headers = dict(self.request.headers)
            headers.pop('Host', None)
            request = httpclient.HTTPRequest(
                'ws://' + url,
                headers=headers,
            )
            self.alter_request(request)
            logger.info("Forwarding websocket connection, url=%r, headers=%r",
                        request.url, request.headers)
            try:
                self.upstream_ws = await websocket_connect(
                    request,
                    on_message_callback=self.on_upstream_message,
                )
            except httpclient.HTTPClientError as e:
                logger.info("Sending HTTP error from websocket client %r %r",
                            e.code, e.message)
                self.set_status(e.code, reason=e.message)
                return self.finish()
            return await WebSocketHandler.get(self)
        else:
            headers = dict(self.request.headers)
            headers.pop('Host', None)
            request = httpclient.HTTPRequest(
                'http://' + url,
                method=self.request.method,
                headers=headers,
                body=self.request.body or None,
                header_callback=self.got_header,
                streaming_callback=self.write,
            )
            self.alter_request(request)
            logger.info("Forwarding HTTP connection, url=%r, headers=%r",
                        request.url, request.headers)
            try:
                await httpclient.AsyncHTTPClient().fetch(
                    request,
                    raise_error=False,
                )
            except OSError:
                logger.info("Got OSError, sending 410 error")
                self.set_status(410)
                self.set_header('Content-Type', 'text/plain')
                return self.finish("This run is now over")
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

    @classmethod
    def make_app(cls, **settings):
        return tornado.web.Application(
            [
                URLSpec('.*', cls),
            ],
            **settings,
        )
