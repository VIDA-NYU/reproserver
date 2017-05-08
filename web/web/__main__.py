import logging
import os
import sys


logging.basicConfig(level=logging.INFO)


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


from web.main import main  # noqa


main()
