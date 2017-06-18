import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


from common.utils import setup_logging  # noqa

setup_logging('REPROSERVER-WEB')

from web.main import main  # noqa

main()
