import json
import logging
import re
from tornado.httpclient import AsyncHTTPClient

from .. import __version__
from .base import BaseRepository, RepositoryError


logger = logging.getLogger(__name__)

_osf_path = re.compile('^[a-zA-Z0-9]+$')


class OSF(BaseRepository):
    IDENTIFIER = 'osf.io'
    URL_DOMAINS = ['osf.io']

    async def parse_url(self, url):
        if url.startswith('http://'):
            url = url[7:]
        elif url.startswith('https://'):
            url = url[8:]
        else:
            raise RepositoryError("Invalid URL")
        if url.lower().startswith('osf.io/'):
            raise RepositoryError("Not OSF URL")

        path = url[7:]
        path = path.rstrip('/')
        if not (3 < len(path) < 10) or '/' in path:
            raise RepositoryError("Invalid OSF URL")
        return 'osf.io', path

    async def get_experiment(self, db, object_store, remote_addr,
                             repo, repo_path):
        if _osf_path.match(repo_path) is None:
            raise RepositoryError("ID is not in the OSF format")
        logger.info("Querying OSF for '%s'", repo_path)
        resp = await AsyncHTTPClient().fetch(
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
            return await self._get_from_link(
                db, object_store, remote_addr,
                repo, repo_path,
                link, filename, filehash,
            )
