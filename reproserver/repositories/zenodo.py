import json
import logging
import os
import re

from .. import __version__
from .base import BaseRepository, RepositoryError


logger = logging.getLogger(__name__)


# Two formats:
# https://zenodo.org/record/3374942 (assumes one RPZ file)
# https://zenodo.org/record/3374942/files/bash-count.rpz
_zenodo_url = re.compile(
    r'https?://zenodo\.org/record/([0-9]+)'
    r'(?:/'
    r'(?:'
    r'files/([^/?]+)'
    r'(?:\?download=1)?'
    r')?'
    r')?$'
)

_zenodo_path = re.compile(r'^([0-9]+)/files/([^/?]+)$')


class Zenodo(BaseRepository):
    IDENTIFIER = 'zenodo.org'
    URL_DOMAINS = ['zenodo.org']

    async def parse_url(self, url):
        m = _zenodo_url.match(url)
        if m is None:
            raise RepositoryError("Invalid Zenodo URL")
        record = m.group(1)
        filename = m.group(2)

        if not filename:
            # Get the list of files, proceed if there's only one RPZ file
            resp = await self.http_client.fetch(
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
                raise RepositoryError("HTTP error from Zenodo")
            try:
                files = json.loads(resp.body.decode('utf-8')).get('files', [])
            except ValueError:
                logger.error("Got invalid JSON from zenodo.org")
                raise RepositoryError("Invalid JSON returned from Zenodo")

            logger.info("Fetched record, %d files", len(files))
            files = [f for f in files
                     if f['filename'].lower().endswith('.rpz')]
            if not files:
                raise RepositoryError("No RPZ file in that deposit")
            elif len(files) > 1:
                raise RepositoryError("Multiple RPZ files in that deposit")
            else:
                filename = files[0]['filename']
                logger.info("Single RPZ selected: %r", filename)

        return 'zenodo.org', '{0}/files/{1}'.format(record, filename)

    def get_experiment(self, db, object_store, remote_addr,
                       repo, repo_path):
        m = _zenodo_path.match(repo_path)
        if m is None:
            raise RepositoryError("Path is not in the Zenodo format")
        return self._get_from_link(
            db, object_store, remote_addr,
            repo, repo_path,
            'https://zenodo.org/record/{0}?download=1'.format(repo_path),
            m.group(2),
        )
