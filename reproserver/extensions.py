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
    # TODO: Should also have a way to re-run on existing RPZ files

    rpz = RPZPack(local_filename)

    # Extract WACZ if present
    if 'web1' in rpz.extensions():
        # Write it to disk
        with tempfile.TemporaryDirectory() as tdir:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: rpz.extract_extension('web1', tdir),
            )
            wacz = os.path.join(tdir, 'archive.wacz')
            extension_files = os.listdir(tdir)
            if extension_files != ['archive.wacz']:
                logger.warning(
                    "Invalid web1 extension data, files: %r",
                    extension_files,
                )
            else:
                # Hash the WACZ
                hasher = sha256()
                with open(wacz, 'rb') as fp:
                    chunk = fp.read(4096)
                    while chunk:
                        hasher.update(chunk)
                        if len(chunk) != 4096:
                            break
                        chunk = fp.read(4096)
                    filehash = hasher.hexdigest()

                # Upload it
                await object_store.upload_file_async(
                    'web1',
                    filehash,
                    os.path.join(tdir, 'archive.wacz'),
                )
                logger.info("Inserted WACZ in storage")

                # Insert it into the database
                extension = database.Extension(
                    experiment=experiment,
                    name='web1',
                    data=json.dumps({'filehash': filehash}),
                )
                db.add(extension)
