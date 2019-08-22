from hashlib import sha256
import logging
import os
import tempfile
from tornado.httpclient import AsyncHTTPClient

from .. import database


logger = logging.getLogger(__name__)


class RepositoryError(Exception):
    pass


class BaseRepository(object):
    IDENTIFIER = ''
    URL_DOMAINS = []

    def parse_url(self, url):
        raise NotImplementedError

    def get_experiment(self, db, object_store, remote_addr,
                       repo, repo_path):
        raise NotImplementedError

    async def _get_from_link(self, db, object_store, remote_addr,
                             repo, repo_path,
                             link, filename, filehash=None):
        # Check for existence of experiment
        if filehash is not None:
            experiment = db.query(database.Experiment).get(filehash)
        else:
            experiment = None
        if experiment:
            logger.info("Experiment with hash exists, no need to download")
        else:
            logger.info("Downloading %s", link)
            fd, local_path = tempfile.mkstemp(prefix='repo_download_')
            try:
                # Download file & hash it
                hasher = sha256()
                with open(local_path, 'wb') as f:
                    def callback(chunk):
                        f.write(chunk)
                        hasher.update(chunk)

                    await AsyncHTTPClient().fetch(
                        link,
                        streaming_callback=callback,
                    )

                filehash = hasher.hexdigest()

                # Check for existence of experiment
                experiment = db.query(database.Experiment).get(filehash)
                if experiment:
                    logger.info("File exists")
                else:
                    # Insert it on S3
                    await object_store.upload_file_async(
                        'experiments', filehash,
                        local_path,
                    )
                    logger.info("Inserted file in storage")

                    # Insert it in database
                    experiment = database.Experiment(hash=filehash)
                    db.add(experiment)
            finally:
                os.close(fd)
                os.remove(local_path)

        # Insert Upload in database
        upload = database.Upload(
            experiment=experiment,
            filename=filename,
            submitted_ip=remote_addr,
            repository_key='%s/%s' % (repo, repo_path),
        )
        db.add(upload)
        db.commit()

        return upload
