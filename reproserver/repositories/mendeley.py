import json
import logging
import os
import re
from urllib.parse import urlencode

from .. import __version__
from .base import BaseRepository, RepositoryError


logger = logging.getLogger(__name__)


_mendeley_url = re.compile(
    r'^https://data\.mendeley\.com'
    r'/datasets/([a-z0-9]{3,15})'  # Dataset ID
    r'(?:/[0-9]+)?'  # Version
    r'(?:'  # Optional, only if link to direct file...
    r'/files/'
    r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'  # File
    r'(?:/.+)?'
    r')?'
    r'$'
)

_mendeley_path = re.compile(
    r'^'
    r'([a-z0-9]{3,15})'
    r'/files/'
    r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
    r'$'
)


class Mendeley(BaseRepository):
    IDENTIFIER = 'data.mendeley.com'
    URL_DOMAINS = ['data.mendeley.com']

    _mendeley_access_token = None

    async def parse_url(self, url):
        m = _mendeley_url.match(url)
        if m is None:
            raise RepositoryError("Invalid Mendeley URL")
        dataset_id = m.group(1)
        file_id = m.group(2)

        if not file_id:
            # Get the list of files, proceed if there's only one RPZ file
            resp = await self.mendeley_get(
                '/datasets/{0}'.format(dataset_id),
            )
            files = resp['files']

            logger.info("Fetched dataset, %d files", len(files))
            files = [f for f in files
                     if f['filename'].lower().endswith('.rpz')]
            if not files:
                raise RepositoryError("No RPZ files in that dataset")
            elif len(files) > 1:
                raise RepositoryError(
                    "Multiple RPZ files in that dataset. Please provide "
                    "a direct link to an RPZ file.")
            else:
                file_id = files[0]['id']

        return (
            'data.mendeley.com',
            '{0}/files/{1}'.format(dataset_id, file_id),
        )

    async def get_experiment(self, db, object_store, remote_addr,
                             repo, repo_path):
        m = _mendeley_path.match(repo_path)
        if m is None:
            raise RepositoryError("Path is not in the Mendeley format")
        resp = await self.mendeley_get(
            '/datasets/{0}/files/{1}'.format(m.group(1), m.group(2)),
        )
        link = resp['content_details']['download_url']
        filehash = resp['content_details']['sha256_hash']
        return await self._get_from_link(
            db, object_store, remote_addr,
            repo, repo_path,
            link,
            filehash,
        )

    async def mendeley_get(self, uri, maybe_refresh_token=True):
        if self._mendeley_access_token is not None:
            resp = await self.http_client.fetch(
                'https://api.mendeley.com' + uri,
                headers={
                    'Accept': 'application/json',
                    'User-Agent': 'reproserver %s' % __version__,
                    'Authorization': 'Bearer {0}'.format(
                        self._mendeley_access_token
                    ),
                },
                raise_error=False,
            )
            if resp.code == 200:
                return json.loads(resp.body.decode('utf-8'))
            elif resp.code != 401:
                raise RepositoryError("HTTP error from Mendeley")

        if maybe_refresh_token:
            # Get new token and try again
            await self.mendeley_get_token()
            return await self.mendeley_get(uri, maybe_refresh_token=False)

    async def mendeley_get_token(self):
        logger.info("Getting access token from Mendeley...")

        # https://dev.mendeley.com/reference/topics/authorization_client_credentials.html
        resp = await self.http_client.fetch(
            'https://api.mendeley.com/oauth/token',
            method='POST',
            headers={
                'User-Agent': 'reproserver %s' % __version__,
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            auth_username=os.environ['MENDELEY_APP_ID'],
            auth_password=os.environ['MENDELEY_SECRET'],
            auth_mode='basic',
            body=urlencode({
                'grant_type': 'client_credentials',
                'scope': 'all',
            }),
            raise_error=False,
        )
        if resp.code != 200:
            logger.error("Error %d refreshing token with Mendeley",
                         resp.code)
            raise RepositoryError("HTTP error from Mendeley")
        obj = json.loads(resp.body.decode('utf-8'))
        self._mendeley_access_token = obj['access_token']
