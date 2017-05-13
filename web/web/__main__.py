import os
import sys
from common.utils import setup_logging


setup_logging('REPROSERVER-WEB')


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


from web.main import main  # noqa


main()
