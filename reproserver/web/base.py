import base64
from hashlib import sha256
import hmac
import importlib
import jinja2
import json
import logging
import os
import pkg_resources
import signal
from streaming_form_data import StreamingFormDataParser
from streaming_form_data.targets import FileTarget, ValueTarget
from tornado.escape import utf8
import tornado.ioloop
import tornado.web

from .. import __version__
from .. import database
from ..objectstore import get_object_store
from ..run.connector import DirectConnector


logger = logging.getLogger(__name__)


class GracefulApplication(tornado.web.Application):
    def __init__(self, *args, **kwargs):
        super(GracefulApplication, self).__init__(*args, **kwargs)

        self.is_exiting = False

        exit_time = os.environ.get('TORNADO_SHUTDOWN_TIME')
        if exit_time:
            exit_time = int(exit_time, 10)
        else:
            exit_time = 30  # Default to 30 seconds

        def exit():
            logger.info("Shutting down")
            tornado.ioloop.IOLoop.current().stop()

        def exit_soon():
            tornado.ioloop.IOLoop.current().call_later(exit_time, exit)

        def signal_handler(signum, frame):
            logger.info("Got SIGTERM")
            self.is_exiting = True
            tornado.ioloop.IOLoop.current().add_callback_from_signal(exit_soon)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)


class Application(GracefulApplication):
    def __init__(self, handlers, **kwargs):
        super(Application, self).__init__(handlers, **kwargs)

        self.DBSession = database.connect(create=True)

        self.object_store = get_object_store()
        self.object_store.create_buckets()

        if 'RUNNER_TYPE' not in os.environ:
            raise RuntimeError("RUNNER_TYPE is not set")
        runner_type = os.environ['RUNNER_TYPE']
        try:
            mod = importlib.import_module('reproserver.run.%s' % runner_type)
            Runner = mod.Runner
        except (ImportError, AttributeError):
            raise ValueError("Couldn't set up RUNNER_TYPE %r" % runner_type)
        self.runner = Runner(
            DirectConnector(
                DBSession=self.DBSession,
                object_store=self.object_store,
            ),
        )

    def log_request(self, handler):
        if handler.request.path == '/health':
            return
        super(Application, self).log_request(handler)


class BaseHandler(tornado.web.RequestHandler):
    """Base class for all request handlers.
    """
    application: Application

    def url_for_upload(self, upload):
        if upload.repository_key is not None:
            repo, repo_path = upload.repository_key.split('/', 1)
            return self.reverse_url('reproduce_repo', repo, repo_path)
        else:
            return self.reverse_url('reproduce_local', upload.short_id)

    template_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(
            [pkg_resources.resource_filename('reproserver', 'web/templates')]
        ),
        autoescape=jinja2.select_autoescape(['html']),
        extensions=['jinja2.ext.i18n'],
    )

    @jinja2.pass_context
    def _tpl_static_url(context, path):
        v = not context['handler'].application.settings.get('debug', False)
        return context['handler'].static_url(path, include_version=v)
    template_env.globals['static_url'] = _tpl_static_url

    @jinja2.pass_context
    def _tpl_reverse_url(context, path, *args):
        return context['handler'].reverse_url(path, *args)
    template_env.globals['reverse_url'] = _tpl_reverse_url

    @jinja2.pass_context
    def _tpl_xsrf_form_html(context):
        return jinja2.Markup(context['handler'].xsrf_form_html())
    template_env.globals['xsrf_form_html'] = _tpl_xsrf_form_html

    @jinja2.pass_context
    def _tpl_url_for_upload(context, upload):
        return context['handler'].url_for_upload(upload)
    template_env.globals['url_for_upload'] = _tpl_url_for_upload

    def __init__(self, application, request, **kwargs):
        super(BaseHandler, self).__init__(application, request, **kwargs)
        self.db = application.DBSession()

    def on_finish(self):
        super(BaseHandler, self).on_finish()
        self.db.close()

    def set_default_headers(self):
        self.set_header('Server', 'ReproServer/%s' % __version__)

    def render_string(self, template_name, **kwargs):
        template = self.template_env.get_template(template_name)
        return template.render(
            handler=self,
            current_user=self.current_user,
            version=__version__,
            page_title=os.environ.get('PAGE_TITLE', 'ReproServer'),
            **kwargs)

    def is_json_requested(self):
        if any(a.lower().startswith('text/html')
               for a in self.request.headers.get('Accept', '').split(',')):
            # Browsers might say they accept JSON, in addition to HTML
            return False
        elif any(a.lower().startswith('application/json') or
                 a.lower().startswith('text/json')
                 for a in self.request.headers.get('Accept', '').split(',')):
            # No HTML and JSON: we should send JSON
            return True
        else:
            # Neither requested, send HTML
            return False

    def get_json(self):
        type_ = self.request.headers.get('Content-Type', '')
        if not type_.startswith('application/json'):
            raise tornado.web.HTTPError(400, "Expected JSON")
        try:
            return json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            raise tornado.web.HTTPError(400, "Invalid JSON")

    def send_json(self, obj):
        if isinstance(obj, list):
            obj = {'results': obj}
        elif not isinstance(obj, dict):
            raise ValueError("Can't encode %r to JSON" % type(obj))
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        return self.finish(json.dumps(obj))

    def send_error_json(self, status, message, reason=None):
        self.set_status(status, reason)
        return self.send_json({'error': message})

    def basic_auth(self, user, password):
        auth_header = self.request.headers.get('Authorization')
        if auth_header is None or not auth_header.startswith('Basic '):
            self.set_status(401)
            self.set_header('WWW-Authenticate', 'Basic realm=reproserver')
            self.finish()
            raise tornado.web.HTTPError(401)
        auth = base64.b64decode(auth_header[6:]).decode('utf-8')
        if auth.split(':', 1) == [user, password]:
            pass
        else:
            self.set_status(401)
            self.set_header('WWW-Authenticate', 'Basic realm=reproserver')
            self.finish()
            raise tornado.web.HTTPError(401)


@tornado.web.stream_request_body
class StreamedRequestHandler(BaseHandler):
    warn_xsrf_not_called = True

    def prepare(self):
        if self.request.method == 'GET':
            raise tornado.web.HTTPError(405)

        self.request.connection.set_max_body_size(10_000_000_000)
        self.streaming_parser = StreamingFormDataParser(self.request.headers)

        self.sent_xsrf_token = ValueTarget()
        self.streaming_parser.register('_xsrf', self.sent_xsrf_token)

        self.register_streaming_targets()

    def register_streaming_targets(self):
        raise NotImplementedError

    def data_received(self, chunk):
        self.streaming_parser.data_received(chunk)

    def check_xsrf_cookie(self):
        pass  # Skip built-in XSRF cookie checking, wait for body

    def check_xsrf_cookie_with_body(self):
        self.warn_xsrf_not_called = False

        token = self.sent_xsrf_token.value.decode('utf-8', 'replace')
        if not token:
            raise tornado.web.HTTPError(
                403,
                "'_xsrf' argument missing from POST",
            )
        _, token, _ = self._decode_xsrf_token(token)
        _, expected_token, _ = self._get_raw_xsrf_token()
        if not token:
            raise tornado.web.HTTPError(
                403,
                "'_xsrf' argument has invalid format",
            )
        if not hmac.compare_digest(utf8(token), utf8(expected_token)):
            raise tornado.web.HTTPError(
                403,
                "XSRF cookie does not match POST argument",
            )

    def post(self):
        self.check_xsrf_cookie_with_body()

    def on_finish(self):
        if self.warn_xsrf_not_called:
            logger.warning("check_xsrf_cookie_with_body() not called")


class HashedFileTarget(FileTarget):
    def __init__(self, *args, hasher=None, **kwargs):
        super(HashedFileTarget, self).__init__(*args, **kwargs)

        if hasher is None:
            self.hasher = sha256()
        else:
            self.hasher = hasher

    def on_data_received(self, chunk):
        super(HashedFileTarget, self).on_data_received(chunk)
        self.hasher.update(chunk)
