import os
import pkg_resources
from tornado.routing import URLSpec

from .base import Application
from . import api
from . import views
from . import webcapture


def make_app(debug=False, xsrf_cookies=True, proxy=None):
    if proxy is not None:
        proxy = [
            URLSpec('/results/(?:[^/]+)/port/(?:[0-9]+)(?:/.*)?', proxy,
                    name='proxy'),
        ]
    else:
        proxy = []

    return Application(
        [
            URLSpec('/', views.Index, name='index'),
            URLSpec('/upload', views.Upload, name='upload'),
            URLSpec('/reproduce/([^/]+)/(.+)', views.ReproduceRepo,
                    name='reproduce_repo'),
            URLSpec('/reproduce/([^/]+)', views.ReproduceLocal,
                    name='reproduce_local'),
            URLSpec('/run/([^/]+)', views.StartRun, name='start_run'),
            URLSpec('/results/([^/]+)', views.Results, name='results'),
            URLSpec('/results/([^/]+)/json', views.ResultsJson,
                    name='results_json'),
            URLSpec('/web', webcapture.Index, name='webcapture_index'),
            URLSpec('/web/upload', webcapture.Upload,
                    name='webcapture_upload'),
            URLSpec('/web/([^/]+)', webcapture.Dashboard,
                    name='webcapture_dashboard'),
            URLSpec('/web/start-record/([^/]+)', webcapture.StartRecord,
                    name='webcapture_start_record'),
            URLSpec('/web/record/([^/]+)/([^/]+)', webcapture.Record,
                    name='webcapture_record'),
            URLSpec('/web/crawl/([^/]+)/start', webcapture.StartCrawl,
                    name='webcapture_start_crawl'),
            URLSpec('/web/crawl/([^/]+)', webcapture.CrawlStatus,
                    name='webcapture_crawl_status'),
            URLSpec('/web/upload-wacz/([^/]+)', webcapture.UploadWacz,
                    name='webcapture_upload_wacz'),
            URLSpec('/web/download/([^/]+)', webcapture.Download,
                    name='webcapture_download'),
            URLSpec('/about', views.About, name='about'),
            URLSpec('/data', views.Data, name='data'),
            URLSpec('/health', views.Health, name='health'),
            URLSpec('/runners/run/([^/]+)/init', api.InitRunGetInfo),
            URLSpec('/runners/run/([^/]+)/start', api.RunStarted),
            URLSpec('/runners/run/([^/]+)/done', api.RunDone),
            URLSpec('/runners/run/([^/]+)/failed', api.RunFailed),
            URLSpec('/runners/run/([^/]+)/output/(.+)', api.UploadOutput),
            URLSpec('/runners/run/([^/]+)/log', api.Log),
        ] + proxy,
        static_path=pkg_resources.resource_filename('reproserver', 'static'),
        xsrf_cookies=xsrf_cookies,
        debug=debug,
        connection_token=os.environ.get('CONNECTION_TOKEN', ''),
    )
