from hashlib import sha256
import json
import logging
import os
import re
import tempfile
from tornado.httpclient import AsyncHTTPClient

from . import __version__
from . import database


__all__ = ['get_experiment_from_provider', 'parse_provider_url']


logger = logging.getLogger(__name__)


class ProviderError(Exception):
    pass


def get_experiment_from_provider(db, object_store, remote_addr,
                                 provider, provider_path):
    try:
        getter = _PROVIDERS[provider]
    except KeyError:
        raise ProviderError("No such provider %s" % provider)
    return getter(db, object_store, remote_addr, provider, provider_path)


# Two formats:
# https://zenodo.org/record/3374942 (assumes one RPZ file)
# https://zenodo.org/record/3374942/files/bash-count.rpz
_zenodo_url = re.compile(
    r'zenodo\.org/record/([0-9]+)'
    r'(?:/'
    r'(?:'
    r'files/([^/?]+)'
    r'(?:\?download=1)?'
    r')?'
    r')?$'
)


async def parse_provider_url(url):
    if url.startswith('http://'):
        url = url[7:]
    elif url.startswith('https://'):
        url = url[8:]
    else:
        raise ProviderError("Invalid URL")

    logger.info("Parsing URL %r", url)

    if url.lower().startswith('osf.io/'):
        path = url[7:]
        path = path.rstrip('/')
        if not (3 < len(path) < 10) or '/' in path:
            raise ProviderError("Invalid OSF URL")
        return 'osf.io', path
    elif url.lower().startswith('zenodo.org'):
        m = _zenodo_url.match(url)
        if m is None:
            raise ProviderError("Invalid Zenodo URL")
        record = int(m.group(1))
        filename = m.group(2)

        if not filename:
            # Get the list of files, proceed if there's only one RPZ file
            resp = await AsyncHTTPClient().fetch(
                'https://zenodo.org/api/deposit/depositions/{0}'.format(
                    record
                ),
                headers={
                    'Accept': 'application/json',
                    'User-Agent': 'reproserver %s' % __version__,
                    'Authorization': 'Bearer {0}'.format(
                        os.environ['ZENODO_TOKEN']
                    ),
                },
                raise_error=False,
            )
            if resp.code != 200:
                logger.info("Got error %s", resp.code)
                raise ProviderError("HTTP error from Zenodo")
            try:
                files = json.loads(resp.body.decode('utf-8')).get('files', [])
            except ValueError:
                logger.error("Got invalid JSON from zenodo.org")
                raise ProviderError("Invalid JSON returned from Zenodo")
            else:
                logger.info("Fetched record, %d files", len(files))
                files = [f for f in files
                         if f['filename'].lower().endswith('.rpz')]
                if not files:
                    raise ProviderError("No RPZ file in that deposit")
                elif len(files) > 1:
                    raise ProviderError("Multiple RPZ files in that deposit")
                else:
                    filename = files[0]['filename']
                    logger.info("Single RPZ selected: %r", filename)

        return 'zenodo.org', '{0}/files/{1}'.format(record, filename)
    elif url.lower().startswith('figshare.com/'):
        path = url[13:]
        # TODO: Actually need to find the article ID, which is not in the URL?
        return 'figshare.com', path
    else:
        raise ProviderError("Unrecognized URL")


async def _get_from_link(db, object_store, remote_addr,
                         provider, provider_path,
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
        fd, local_path = tempfile.mkstemp(prefix='provider_download_')
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
    upload = database.Upload(experiment=experiment,
                             filename=filename,
                             submitted_ip=remote_addr,
                             provider_key='%s/%s' % (provider, provider_path))
    db.add(upload)
    db.commit()

    return upload


# Providers


_osf_path = re.compile('^[a-zA-Z0-9]+$')


async def _osf(db, object_store, remote_addr, provider, path):
    if _osf_path.match(path) is None:
        raise ProviderError("ID is not in the OSF format")
    logger.info("Querying OSF for '%s'", path)
    resp = await AsyncHTTPClient().fetch(
        'https://api.osf.io/v2/files/{0}/'.format(path),
        headers={
            'Accept': 'application/json',
            'User-Agent': 'reproserver %s' % __version__,
        },
        raise_error=False,
    )
    if resp.code != 200:
        logger.info("Got error %s", resp.code)
        raise ProviderError("HTTP error from OSF")
    try:
        response = json.loads(resp.body.decode('utf-8'))
        link = response['data']['links']['download']
    except KeyError:
        raise ProviderError("Invalid data returned from the OSF")
    except ValueError:
        logger.error("Got invalid JSON from osf.io")
        raise ProviderError("Invalid JSON returned from the OSF")
    else:
        try:
            attrs = response['data']['attributes']
            filehash = attrs['extra']['hashes']['sha256']
        except KeyError:
            filehash = None
        try:
            filename = response['data']['attributes']['name']
        except KeyError:
            filename = 'unnamed_osf_file'
        logger.info("Got response: %s %s %s", link, filehash, filename)
        return await _get_from_link(
            db, object_store, remote_addr,
            provider, path,
            link, filename, filehash,
        )


_zenodo_path = re.compile(r'^([0-9]+)/files/([^/?]+)$')


def _zenodo(db, object_store, remote_addr, provider, path):
    m = _zenodo_path.match(path)
    if m is None:
        raise ProviderError("Path is not in the Zenodo format")
    return _get_from_link(
        db, object_store, remote_addr,
        provider, path,
        'https://zenodo.org/record/{0}?download=1'.format(path),
        m.group(2),
    )


async def _figshare(db, object_store, remote_addr, provider, path):
    # article_id/file_id
    try:
        article_id, file_id = path.split('/', 1)
        article_id = int(article_id)
        file_id = int(file_id)
    except ValueError:
        raise ProviderError("ID is not in 'article_id/file_id' format")
    logger.info("Querying Figshare for article=%s file=%s",
                article_id, file_id)
    resp = await AsyncHTTPClient().fetch(
        'https://api.figshare.com/v2/articles/{0}/files/{1}'.format(
            article_id, file_id,
        ),
        headers={
            'Accept': 'application/json',
            'User-Agent': 'reproserver %s' % __version__,
        },
        raise_error=False,
    )
    if resp.code != 200:
        logger.info("Got error %s", resp.code)
        raise ProviderError("HTTP error from Figshare")
    try:
        response = json.loads(resp.body.decode('utf-8'))
        link = response['download_url']
    except KeyError:
        raise ProviderError("Invalid data returned from Figshare")
    except ValueError:
        logger.error("Got invalid JSON from Figshare")
        raise ProviderError("Invalid JSON returned from Figshare")
    else:
        try:
            filename = response['name']
        except KeyError:
            filename = 'unnamed_figshare_file'
        logger.info("Got response: %s %s", link, filename)
        return await _get_from_link(
            db, object_store, remote_addr,
            provider, path,
            link, filename,
        )


_PROVIDERS = {
    'osf.io': _osf,
    'zenodo.org': _zenodo,
    'figshare.com': _figshare,
}
