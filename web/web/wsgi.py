import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


from common.utils import setup_logging
from web.main import app  # noqa


setup_logging('REPROSERVER-WEB')
application = app

__all__ = ['application']
