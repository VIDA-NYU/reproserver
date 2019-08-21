from common import database, get_object_store
from hashlib import sha256
import logging
import os
import re
import requests
import tempfile


__all__ = ['get_experiment_from_provider']


class ProviderError(Exception):
    pass


def get_experiment_from_provider(session, remote_addr,
                                 provider, provider_path):
    try:
        getter = _PROVIDERS[provider]
    except KeyError:
        raise ProviderError("No such provider %s" % provider)
    return getter(session, remote_addr, provider, provider_path)


def _get_from_link(session, remote_addr, provider, provider_path,
                   link, filename, filehash=None):
    # Check for existence of experiment
    if filehash is not None:
        experiment = session.query(database.Experiment).get(filehash)
    else:
        experiment = None
    if experiment:
        logging.info("Experiment with hash exists, no need to download")
    else:
        logging.info("Downloading %s", link)
        fd, local_path = tempfile.mkstemp(prefix='provider_download_')
        try:
            # Download file & hash it
            response = requests.get(link, stream=True)
            response.raise_for_status()
            hasher = sha256()
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(4096):
                    f.write(chunk)
                    hasher.update(chunk)

            filehash = hasher.hexdigest()

            # Check for existence of experiment
            experiment = session.query(database.Experiment).get(filehash)
            if experiment:
                logging.info("File exists")
            else:
                # Insert it on S3
                object_store = get_object_store()
                object_store.upload_file('experiments', filehash,
                                         local_path)
                logging.info("Inserted file in storage")

                # Insert it in database
                experiment = database.Experiment(hash=filehash)
                session.add(experiment)
        finally:
            os.close(fd)
            os.remove(local_path)

    # Insert Upload in database
    upload = database.Upload(experiment=experiment,
                             filename=filename,
                             submitted_ip=remote_addr,
                             provider_key='%s/%s' % (provider, provider_path))
    session.add(upload)
    session.commit()

    return upload


# Providers


_osf_path = re.compile('^[a-zA-Z0-9]+$')


def _osf(session, remote_addr, provider, path):
    if _osf_path.match(path) is None:
        raise ProviderError("ID is not in the OSF format")
    logging.info("Querying OSF for '%s'", path)
    req = requests.get('https://api.osf.io/v2/files/{0}/'.format(path),
                       headers={'Content-Type': 'application/json',
                                'Accept': 'application/json'})
    if req.status_code != 200:
        logging.info("Got error %s", req.status_code)
        raise ProviderError("HTTP error from OSF")
    try:
        response = req.json()
        link = response['data']['links']['download']
    except KeyError:
        raise ProviderError("Invalid data returned from the OSF")
    except ValueError:
        logging.error("Got invalid JSON from osf.io")
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
        logging.info("Got response: %s %s %s", link, filehash, filename)
        return _get_from_link(session, remote_addr, provider, path,
                              link, filename, filehash)


def _figshare(session, remote_addr, provider, path):
    # article_id/file_id
    try:
        article_id, file_id = path.split('/', 1)
        article_id = int(article_id)
        file_id = int(file_id)
    except ValueError:
        raise ProviderError("ID is not in 'article_id/file_id' format")
    logging.info("Querying Figshare for article=%s file=%s",
                 article_id, file_id)
    req = requests.get('https://api.figshare.com/v2/articles/{0}/files/{1}'
                       .format(article_id, file_id),
                       headers={'Accept': 'application/json'})
    if req.status_code != 200:
        logging.info("Got error %s", req.status_code)
        raise ProviderError("HTTP error from Figshare")
    try:
        response = req.json()
        link = response['download_url']
    except KeyError:
        raise ProviderError("Invalid data returned from Figshare")
    except ValueError:
        logging.error("Got invalid JSON from Figshare")
        raise ProviderError("Invalid JSON returned from Figshare")
    else:
        try:
            filename = response['name']
        except KeyError:
            filename = 'unnamed_figshare_file'
        logging.info("Got response: %s %s", link, filename)
        return _get_from_link(session, remote_addr, provider, path,
                              link, filename)


_PROVIDERS = {
    'osf.io': _osf,
    'figshare.com': _figshare,
}
