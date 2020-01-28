import json
import logging
import subprocess

from . import database
from .utils import shell_escape


logger = logging.getLogger(__name__)


def make_experiment(filehash, filename):
    # Insert it in database
    experiment = database.Experiment(hash=filehash)

    # Extract metadata
    info_proc = subprocess.Popen(
        ['reprounzip', 'info', '--json', filename],
        stdout=subprocess.PIPE,
    )
    info_stdout, _ = info_proc.communicate()
    if info_proc.wait() != 0:
        raise ValueError("Error getting info from package")
    info = json.loads(info_stdout.decode('utf-8'))
    logger.info("Got metadata, %d runs", len(info['runs']))

    # Add parameters
    # Command-line of each run
    for i, run in enumerate(info['runs']):
        cmdline = ' '.join(shell_escape(a) for a in run['argv'])
        experiment.parameters.append(database.Parameter(
            name="cmdline_%05d" % i, optional=False, default=cmdline,
            description="Command-line for step %s" % run['id'],
        ))
    # Input/output files
    for name, iofile in info.get('inputs_outputs', ()).items():
        path = iofile['path']

        # It's an input if it's read before it is written
        if iofile['read_runs'] and iofile['write_runs']:
            first_write = min(iofile['write_runs'])
            first_read = min(iofile['read_runs'])
            is_input = first_read <= first_write
        else:
            is_input = bool(iofile['read_runs'])

        # It's an output if it's ever written
        is_output = bool(iofile['write_runs'])

        experiment.paths.append(database.Path(
            is_input=is_input,
            is_output=is_output,
            name=name,
            path=path,
        ))

    return experiment
