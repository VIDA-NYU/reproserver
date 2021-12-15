import asyncio
import contextlib
import logging
import os
import re
import subprocess
import sys


logger = logging.getLogger(__name__)


@contextlib.contextmanager
def prom_incremented(metric, amount=1):
    """Context manager that increments a metric, then decrements it at the end.
    """
    metric.inc(amount)
    try:
        yield
    finally:
        metric.dec(amount)


async def subprocess_call_async(cmdline):
    proc = await asyncio.create_subprocess_exec(*cmdline)
    return await proc.wait()


async def subprocess_check_call_async(cmdline):
    proc = await asyncio.create_subprocess_exec(*cmdline)
    ret = await proc.wait()
    if ret != 0:
        raise subprocess.CalledProcessError(ret, cmdline)


safe_shell_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                       "abcdefghijklmnopqrstuvwxyz"
                       "0123456789"
                       "-+=/:.,%_")


def shell_escape(s):
    r"""Given bl"a, returns "bl\\"a".
    """
    if isinstance(s, bytes):
        s = s.decode('utf-8')
    if not s or any(c not in safe_shell_chars for c in s):
        return '"%s"' % (s.replace('\\', '\\\\')
                         .replace('"', '\\"')
                         .replace('`', '\\`')
                         .replace('$', '\\$'))
    else:
        return s


_windows_device_files = ('CON', 'AUX', 'COM1', 'COM2', 'COM3', 'COM4', 'LPT1',
                         'LPT2', 'LPT3', 'PRN', 'NUL')
_not_ascii_re = re.compile(r'[^A-Za-z0-9_.-]')


def secure_filename(name):
    """Sanitize a filename.

    This takes a filename, for example provided by a browser with a file
    upload, and turn it into something that is safe for opening.

    Adapted from werkzeug's secure_filename(), copyright 2007 the Pallets team.
    https://palletsprojects.com/p/werkzeug/
    """
    if '/' in name:
        name = name[name.rindex('/') + 1:]
    if secure_filename.windows and '\\' in name:
        # It seems that IE gets that wrong, at least when the file is from
        # a network share
        name = name[name.rindex('\\') + 1:]
    name, ext = os.path.splitext(name)
    name = name[:20]
    name = _not_ascii_re.sub('', name).strip('._')
    if not name:
        name = '_'
    ext = _not_ascii_re.sub('', ext)
    if (secure_filename.windows and
            name.split('.')[0].upper() in _windows_device_files):
        name = '_' + name
    name = name + ext
    return name


secure_filename.windows = os.name == 'nt'


_futures = dict()


def background_future(future, *, should_never_exit=False):
    """Workaround for https://bugs.python.org/issue21163

    Adding a callback to a future and throwing it out cancels the task. Call
    this function to avoid this.
    """
    future = asyncio.ensure_future(future)
    future_id = id(future)
    _futures[future_id] = future_id

    @future.add_done_callback
    def callback(f):
        # Call result to avoid warnings
        try:
            f.result()
        except Exception:
            logger.exception("Error in background task")
        if should_never_exit:
            logger.critical("Critical task died, exiting")
            asyncio.get_event_loop().stop()
            sys.exit(1)
        _futures.pop(future_id, None)
