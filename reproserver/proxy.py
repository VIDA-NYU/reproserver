import itertools
import logging
import os
import prometheus_client
import re
from tornado import httputil
from tornado import httpclient
import tornado.ioloop
from tornado.routing import URLSpec
import tornado.web
from tornado.websocket import WebSocketHandler, websocket_connect

from . import __version__
from . import database
from .utils import setup
from .web.base import GracefulApplication, HideStreamClosedHandler


logger = logging.getLogger(__name__)


PROM_PROXY_REQUESTS = prometheus_client.Counter(
    'proxy_requests_total',
    "Proxy requests",
    ['proto', 'status'],
)
for args in itertools.product(['http', 'ws'], ['success', 'error']):
    PROM_PROXY_REQUESTS.labels(*args).inc(0)


class IsKubernetesProbe(tornado.routing.Matcher):
    def match(self, request):
        if 'X-Kubernetes-Probe' in request.headers:
            return {}

        return None


class Health(tornado.web.RequestHandler):
    def get(self):
        self.set_header('Content-Type', 'text/plain')

        # We're not ready if we've been asked to shut down
        if self.application.is_exiting:
            self.set_status(503, "Shutting down")
            return self.finish('Shutting down')

        return self.finish('Ok')


class ProxyApplication(GracefulApplication):
    def __init__(self, handler, **settings):
        super(ProxyApplication, self).__init__(
            [
                (
                    IsKubernetesProbe(),
                    [
                        URLSpec('/health', Health),
                    ],
                ),
                URLSpec('.*', handler),
            ],
            **settings,
        )

    def log_request(self, handler):
        if isinstance(handler, Health):
            return
        super(GracefulApplication, self).log_request(handler)


class SubdirRewriteMixin:
    def set_status(self, code, reason=None):
        if code >= 300 and code < 400:
            logger.info(
                "Rewrite status %d %s -> 200 OK, add as headers",
                code,
                reason,
            )
            super(SubdirRewriteMixin, self).set_status(200, "OK")
            self.set_header("x-redirect-status", str(code))
            self.set_header("x-redirect-statusText", reason)
        else:
            super(SubdirRewriteMixin, self).set_status(code, reason)

    def set_header(self, name, value):
        if name and name.lower() == "location":
            name = "x-orig-location"
            logger.info("Rewrite location -> x-orig-location")

        super(SubdirRewriteMixin, self).set_header(name, value)


class ProxyHandler(HideStreamClosedHandler, WebSocketHandler):
    def __init__(self, application, request, **kwargs):
        super(ProxyHandler, self).__init__(application, request, **kwargs)
        self.headers = []

    def check_xsrf_cookie(self):
        pass

    def set_default_headers(self):
        self.set_header('Server', 'ReproServer/%s' % __version__)

    def check_etag_header(self):
        return False

    def set_etag_header(self):
        pass

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
                follow_redirects=False,
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
                PROM_PROXY_REQUESTS.labels('ws', 'error').inc()
                self.set_status(e.code, reason=e.message)
                return await self.finish()
            PROM_PROXY_REQUESTS.labels('ws', 'success').inc()
            return await WebSocketHandler.get(self)
        else:
            def write(chunk):
                self.write(chunk)
                self.flush()

            headers = dict(self.request.headers)
            headers.pop('Host', None)
            request = httpclient.HTTPRequest(
                'http://' + url,
                method=self.request.method,
                headers=headers,
                follow_redirects=False,
                body=self.request.body or None,
                header_callback=self.got_header,
                streaming_callback=write,
            )
            self.alter_request(request)
            logger.info("Forwarding HTTP connection, url=%r, headers=%r",
                        request.url, request.headers)
            try:
                await httpclient.AsyncHTTPClient().fetch(
                    request,
                    raise_error=False,
                )
            except Exception:
                PROM_PROXY_REQUESTS.labels('http', 'error').inc()
                # Host resolves but doesn't answer
                logger.info("Host doesn't reply, sending 503")
                self.set_status(503)
                self.set_header('Content-Type', 'text/plain')
                return await self.finish(
                    "This run is not responding, it might be starting up "
                    + "or have already ended",
                )

            PROM_PROXY_REQUESTS.labels('http', 'success').inc()
            return await self.finish()

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
                if name.lower() in (
                    'content-length', 'connection', 'transfer-encoding',
                ):
                    continue
                self.set_header(name, value.strip())
            self.flush()

    def on_ws_connection_close(self, close_code=None, close_reason=None):
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
        return ProxyApplication(cls, **settings)


class DockerProxyHandler(ProxyHandler):
    def select_destination(self):
        # Read destination from hostname
        self.original_host = self.request.host
        host_name = self.request.host_name.split('.', 1)[0]
        parts = host_name.rsplit('-', 1)
        if len(parts) != 2:
            self.set_status(403)
            logger.info("Invalid hostname")
            self.finish("Invalid hostname")
            return
        run_short_id, port = parts
        database.Run.decode_id(run_short_id)

        url = 'docker:{0}{1}'.format(port, self.request.uri)
        return url

    def alter_request(self, request):
        request.headers['Host'] = self.original_host


class DockerSubdirProxyHandler(SubdirRewriteMixin, DockerProxyHandler):
    _re_path = re.compile(r'^/?results/([^/]+)/port/([0-9]+)')

    def select_destination(self):
        self.original_host = self.request.host

        # Read destination from path
        m = self._re_path.match(self.request.path)
        if m is None:
            return
        run_short_id, port = m.groups()
        database.Run.decode_id(run_short_id)

        uri = self.request.uri
        uri = self._re_path.sub('', uri)
        url = 'docker:{0}{1}'.format(port, uri)
        return url


class K8sProxyHandler(ProxyHandler):
    def select_destination(self):
        # Read destination from hostname
        self.original_host = self.request.host
        host_name = self.request.host_name.split('.', 1)[0]
        parts = host_name.split('-')
        if len(parts) != 2:
            self.set_status(403)
            logger.info("Invalid hostname")
            self.finish("Invalid hostname")
            return
        run_short_id, self.target_port = parts
        run_id = database.Run.decode_id(run_short_id)

        url = '{0}run-{1}:5597{2}'.format(
            os.environ['RUN_NAME_PREFIX'],
            run_id,
            self.request.uri,
        )
        return url

    def alter_request(self, request):
        # Authentication
        request.headers['X-Reproserver-Authenticate'] = \
            self.application.settings['connection_token']

        request.headers['Host'] = self.original_host
        request.headers['X-Reproserver-Port'] = self.target_port

    @classmethod
    def make_app(cls, **settings):
        return super(K8sProxyHandler, cls).make_app(
            connection_token=os.environ['CONNECTION_TOKEN'],
            **settings,
        )


class K8sSubdirProxyHandler(SubdirRewriteMixin, K8sProxyHandler):
    _re_path = re.compile(r'^/?results/([^/]+)/port/([0-9]+)')

    def select_destination(self):
        if self.request.headers.get('Sec-Fetch-Site', None) != 'same-origin':
            self.set_status(500)
            logger.info("Non-service-worker request to subdir proxy")
            self.finish("Non-service-worker request to subdir proxy")
            return

        self.original_host = self.request.host

        # Read destination from path
        m = self._re_path.match(self.request.path)
        if m is None:
            return
        run_short_id, self.target_port = m.groups()
        run_id = database.Run.decode_id(run_short_id)

        uri = self.request.uri
        uri = self._re_path.sub('', uri)
        url = '{0}run-{1}:5597{2}'.format(
            os.environ['RUN_NAME_PREFIX'],
            run_id,
            uri,
        )
        return url

    def alter_request(self, request):
        super(K8sSubdirProxyHandler, self).alter_request(request)
        if 'X-Proxy-Host' in request.headers:
            request.headers['Host'] = request.headers.pop('X-Proxy-Host')


def docker_proxy():
    setup()

    # Database connection is not used, but we still need to prime short ids
    database.connect()

    proxy = DockerProxyHandler.make_app()
    proxy.listen(8001, address='0.0.0.0', xheaders=True)
    loop = tornado.ioloop.IOLoop.current()
    loop.start()


def k8s_proxy():
    setup()

    # Database connection is not used, but we still need to prime short ids
    database.connect()

    proxy = K8sProxyHandler.make_app()
    proxy.listen(8001, address='0.0.0.0', xheaders=True)
    loop = tornado.ioloop.IOLoop.current()
    loop.start()
