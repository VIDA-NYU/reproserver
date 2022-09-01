import asyncio
import json
import logging
from reprounzip.pack_info import get_package_info

from . import database
from .utils import shell_escape


logger = logging.getLogger(__name__)


class InvalidPackage(ValueError):
    """The RPZ package is invalid, can't get metadata from it.
    """


def get_metadata(filename):
    try:
        info = get_package_info(filename)
    except Exception as e:
        raise InvalidPackage(
            "Error getting info from package (is it an RPZ file?)"
        ) from e
    logger.info("Got metadata, %d runs", len(info['runs']))

    compact_json = json.dumps(
        info,
        sort_keys=True, separators=(',', ':'),
    )

    return info, compact_json


async def make_experiment(filehash, filename):
    # Insert it in database
    experiment = database.Experiment(hash=filehash)

    # Extract metadata
    info, experiment.info = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: get_metadata(filename),
    )

    # Add parameters
    # Command-line of each run
    for i, run in enumerate(info['runs']):
        cmdline = ' '.join(shell_escape(a) for a in run['argv'])
        experiment.parameters.append(database.Parameter(
            name="cmdline_%05d" % i, optional=True, default=cmdline,
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
