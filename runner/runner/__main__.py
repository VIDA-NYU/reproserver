import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


from runner.main import main  # noqa


main()
