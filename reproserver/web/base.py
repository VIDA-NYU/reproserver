import jinja2
import json
import logging
import mimetypes
import pkg_resources
import tornado.web

from .. import __version__ as version
from .. import database
from ..build import K8sBuilder
from ..objectstore import get_object_store
from ..run import Runner


logger = logging.getLogger(__name__)


class Application(tornado.web.Application):
    def __init__(self, handlers, **kwargs):
        super(Application, self).__init__(handlers, **kwargs)

        engine, self.DBSession = database.connect()
        if not engine.dialect.has_table(engine.connect(), 'experiments'):
            logging.warning("The tables don't seem to exist; creating")
            database.Base.metadata.create_all(bind=engine)

        self.object_store = get_object_store()
        self.object_store.create_buckets()

        self.builder = K8sBuilder(
            DBSession=self.DBSession,
            object_store=self.object_store,
            namespace='default',
        )
        self.runner = Runner(self.DBSession, self.object_store)


class BaseHandler(tornado.web.RequestHandler):
    """Base class for all request handlers.
    """
    def url_for_upload(self, upload):
        if upload.repository_key is not None:
            repo, repo_path = upload.repository_key.split('/', 1)
            return self.reverse_url('reproduce_repo', repo, repo_path)
        else:
            return self.reverse_url('reproduce_local', upload.short_id)

    def output_link(self, output_file):
        path = self.db.query(database.Path).filter(
            database.Path.experiment_hash == output_file.run.experiment_hash,
            database.Path.name == output_file.name,
        ).one().path
        mime = mimetypes.guess_type(path)[0]
        return self.application.object_store.presigned_serve_url(
            'outputs', output_file.hash,
            output_file.name,
            mime,
        )

    template_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(
            [pkg_resources.resource_filename('reproserver', 'templates')]
        ),
        autoescape=jinja2.select_autoescape(['html']),
        extensions=['jinja2.ext.i18n'],
    )

    @jinja2.contextfunction
    def _tpl_static_url(context, path):
        v = not context['handler'].application.settings.get('debug', False)
        return context['handler'].static_url(path, include_version=v)
    template_env.globals['static_url'] = _tpl_static_url

    @jinja2.contextfunction
    def _tpl_reverse_url(context, path, *args):
        return context['handler'].reverse_url(path, *args)
    template_env.globals['reverse_url'] = _tpl_reverse_url

    @jinja2.contextfunction
    def _tpl_xsrf_form_html(context):
        return jinja2.Markup(context['handler'].xsrf_form_html())
    template_env.globals['xsrf_form_html'] = _tpl_xsrf_form_html

    @jinja2.contextfunction
    def _tpl_url_for_upload(context, upload):
        return context['handler'].url_for_upload(upload)
    template_env.globals['url_for_upload'] = _tpl_url_for_upload

    @jinja2.contextfunction
    def _tpl_output_link(context, output_file):
        return context['handler'].output_link(output_file)
    template_env.globals['output_link'] = _tpl_output_link

    def __init__(self, application, request, **kwargs):
        super(BaseHandler, self).__init__(application, request, **kwargs)
        self.db = application.DBSession()

    def on_finish(self):
        super(BaseHandler, self).on_finish()
        self.db.close()

    def render_string(self, template_name, **kwargs):
        template = self.template_env.get_template(template_name)
        return template.render(
            handler=self,
            current_user=self.current_user,
            version=version,
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
