from hashlib import sha256
import logging
import tempfile
from tornado.httpclient import AsyncHTTPClient

from .. import database
from .. import rpz_metadata


logger = logging.getLogger(__name__)


class RepositoryError(Exception):
    pass


class RepositoryUnknown(RepositoryError):
    pass


async def get_from_link(db, object_store, remote_addr,
                        repo, repo_path,
                        link, filename, *, filehash=None, http_client=None):
    if http_client is None:
        http_client = AsyncHTTPClient()

    # Check for existence of experiment
    if filehash is not None:
        experiment = db.query(database.Experiment).get(filehash)
    else:
        experiment = None
    if experiment:
        logger.info("Experiment with hash exists, no need to download")
    else:
        logger.info("Downloading %s", link)
        with tempfile.NamedTemporaryFile(
                'w+b', prefix='repo_download_',
        ) as tfile:
            # Download file & hash it
            hasher = sha256()

            def callback(chunk):
                tfile.write(chunk)
                hasher.update(chunk)

            await http_client.fetch(
                link,
                streaming_callback=callback,
            )
            tfile.flush()

            filehash = hasher.hexdigest()
            logger.info("Downloaded, hash: %s", filehash)

            # Check for existence of experiment
            experiment = db.query(database.Experiment).get(filehash)
            if experiment:
                logger.info("File exists")
            else:
                # Insert it in database
                # Might raise rpz_metadata.InvalidPackage
                experiment = await rpz_metadata.make_experiment(
                    filehash,
                    tfile.name,
                )
                db.add(experiment)

                # Insert it on S3
                await object_store.upload_file_async(
                    'experiments', filehash,
                    tfile.name,
                )
                logger.info("Inserted file in storage")

    # Insert Upload in database
    upload = database.Upload(
        experiment=experiment,
        filename=filename,
        submitted_ip=remote_addr,
        repository_key='%s/%s' % (repo, repo_path) if repo else None,
    )
    db.add(upload)
    db.commit()

    return upload


class BaseRepository(object):
    IDENTIFIER = ''
    NAME = '(unknown repository)'
    URL_DOMAINS = []

    def __init__(self):
        self.http_client = AsyncHTTPClient()

    def parse_url(self, url):
        raise NotImplementedError

    def get_experiment(self, db, object_store, remote_addr,
                       repo, repo_path):
        raise NotImplementedError

    async def get_page_url(self, repo, repo_path):
        return None

    def _get_from_link(
            self, db, object_store, remote_addr,
            repo, repo_path,
            link, filename, *, filehash=None,
    ):
        return get_from_link(
            db, object_store, remote_addr,
            repo, repo_path,
            link, filename, filehash=filehash, http_client=self.http_client,
        )
