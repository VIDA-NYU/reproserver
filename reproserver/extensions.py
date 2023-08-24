import asyncio
from hashlib import sha256
import json
import logging
import os
from reprozip_core.common import RPZPack
import tempfile

from . import database


logger = logging.getLogger(__name__)


async def process_uploaded_rpz(object_store, db, experiment, local_filename):
    """Do additional processing from uploaded RPZ.
    """
    # TODO: Should probably be rewritten in a plugin manner

    rpz = RPZPack(local_filename)

    # Extract WACZ if present
    if 'web1' in rpz.extensions():
        # Write it to disk
        with tempfile.TemporaryDirectory() as tdir:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: rpz.extract_extension('web1', tdir),
            )
            extension_files = set(os.listdir(tdir))
            if (
                'archive.wacz' not in extension_files
                or not extension_files <= {'archive.wacz', 'config.json'}
            ):
                logger.warning(
                    "Invalid web1 extension data, files: %r",
                    extension_files,
                )
            else:
                # Load the config
                config_file = os.path.join(tdir, 'config.json')
                if os.path.exists(config_file):
                    with open(config_file) as fp:
                        config = json.load(fp)
                else:
                    config = None

                # Hash the WACZ
                wacz = os.path.join(tdir, 'archive.wacz')
                hasher = sha256()
                with open(wacz, 'rb') as fp:
                    chunk = fp.read(4096)
                    while chunk:
                        hasher.update(chunk)
                        if len(chunk) != 4096:
                            break
                        chunk = fp.read(4096)
                    filehash = hasher.hexdigest()
                    filesize = fp.tell()

                # Upload it
                await object_store.upload_file_async(
                    'web1',
                    filehash + '.wacz',
                    os.path.join(tdir, 'archive.wacz'),
                )
                logger.info("Inserted WACZ in storage")

                # Insert it into the database
                extension = database.Extension(
                    experiment=experiment,
                    name='web1',
                    data=json.dumps({
                        'filehash': filehash,
                        'filesize': filesize,
                        'config': config,
                    }),
                )
                db.add(extension)
