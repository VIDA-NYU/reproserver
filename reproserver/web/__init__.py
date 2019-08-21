import pkg_resources
from tornado.routing import URLSpec

from .base import Application
from . import views


def make_app(config, debug=False, xsrf_cookies=True):
    return Application(
        [
            URLSpec('/', views.Index, name='index'),
        ],
        static_path=pkg_resources.resource_filename('reproserver', 'static'),
        xsrf_cookies=xsrf_cookies,
        debug=debug,
        config=config,
    )
