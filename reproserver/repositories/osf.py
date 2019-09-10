import json
import logging
import re

from .. import __version__
from .base import BaseRepository, RepositoryError


logger = logging.getLogger(__name__)


# https://osf.io/5ztp2/
# https://osf.io/5ztp2/download/
_osf_url = re.compile(
    r'https?://osf\.io/'
    r'([a-zA-Z0-9]{3,10})'
    r'(?:/(?:download/?)?)?$'
)

_osf_path = re.compile('^[a-zA-Z0-9]{3,10}$')


class OSF(BaseRepository):
    IDENTIFIER = 'osf.io'
    URL_DOMAINS = ['osf.io']

    async def parse_url(self, url):
        m = _osf_url.match(url)
        if m is None:
            raise RepositoryError("Not OSF URL")
        path = m.group(1)

        if not (3 < len(path) < 10) or '/' in path:
            raise RepositoryError("Invalid OSF URL")
        return 'osf.io', path

    async def get_experiment(self, db, object_store, remote_addr,
                             repo, repo_path):
        if _osf_path.match(repo_path) is None:
            raise RepositoryError("ID is not in the OSF format")
        logger.info("Querying OSF for '%s'", repo_path)
        resp = await self.http_client.fetch(
            'https://api.osf.io/v2/files/{0}/'.format(repo_path),
            headers={
                'Accept': 'application/json',
                'User-Agent': 'reproserver %s' % __version__,
            },
            raise_error=False,
        )
        if resp.code != 200:
            logger.info("Got error %s", resp.code)
            raise RepositoryError("HTTP error from OSF")
        try:
            response = json.loads(resp.body.decode('utf-8'))
            link = response['data']['links']['download']
        except KeyError:
            raise RepositoryError("Invalid data returned from the OSF")
        except ValueError:
            logger.error("Got invalid JSON from osf.io")
            raise RepositoryError("Invalid JSON returned from the OSF")

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
        return await self._get_from_link(
            db, object_store, remote_addr,
            repo, repo_path,
            link, filename, filehash,
        )
