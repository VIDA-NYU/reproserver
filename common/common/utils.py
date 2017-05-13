from datetime import datetime
import logging


safe_shell_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                       "abcdefghijklmnopqrstuvwxyz"
                       "0123456789"
                       "-+=/:.,%_")


def shell_escape(s):
    r"""Given bl"a, returns "bl\\"a".
    """
    if not isinstance(s, bytes):
        s = s.encode('utf-8')
    if not s or any(c not in safe_shell_chars for c in s):
        return '"%s"' % (s.replace('\\', '\\\\')
                          .replace('"', '\\"')
                          .replace('`', '\\`')
                          .replace('$', '\\$'))
    else:
        return s


class LoggingDateFormatter(logging.Formatter):
    """Formatter that puts milliseconds in the timestamp.
    """
    converter = datetime.fromtimestamp

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        t = ct.strftime("%H:%M:%S")
        s = "%s.%03d" % (t, record.msecs)
        return s


def setup_logging(tag):
    """Sets up the logging module.
    """
    # Create formatter, with same format as C extension
    fmt = "[%s] %%(asctime)s %%(levelname)s: %%(message)s" % tag
    formatter = LoggingDateFormatter(fmt)

    # Console logger
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(formatter)

    # Set up logger
    logger = logging.root
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
