import json
import logging
import re

from .. import __version__
from .base import BaseRepository, RepositoryError


logger = logging.getLogger(__name__)


# One format:
# https://figshare.com/articles/name_name/3546675 (assumes one RPZ file)
# There is no URL that includes both article and file
# Direct link to files look like this (but no way to use API from that):
# https://ndownloader.figshare.com/files/5612292
_figshare_url = re.compile(
    r'https?://figshare\.com/articles/[^/]+/([0-9]+)$'
)

_figshare_path = re.compile(r'^([0-9]+)/files/([0-9]+)$')


class Figshare(BaseRepository):
    IDENTIFIER = 'figshare.com'
    URL_DOMAINS = ['figshare.com']

    async def parse_url(self, url):
        m = _figshare_url.match(url)
        if m is None:
            raise RepositoryError("Invalid Figshare URL")
        article = m.group(1)

        # Get the list of files, proceed if there's only one RPZ file
        resp = await self.http_client.fetch(
            'https://api.figshare.com/v2/articles/{0}/files'.format(article),
            headers={
                'Accept': 'application/json',
                'User-Agent': 'reproserver %s' % __version__,
            },
            raise_error=False,
        )
        if resp.code != 200:
            logger.info("Got error %s", resp.code)
            raise RepositoryError("HTTP error from Figshare")
        try:
            files = json.loads(resp.body.decode('utf-8'))
        except ValueError:
            logger.error("Got invalid JSON from figshare.com")
            raise RepositoryError("Invalid JSON returned from Figshare")

        logger.info("Fetched file list, %d files", len(files))
        files = [f for f in files
                 if f['name'].lower().endswith('.rpz')]
        if not files:
            raise RepositoryError("No RPZ file in that item")
        elif len(files) > 1:
            raise RepositoryError("Multiple RPZ files in that item")
        else:
            file_id = files[0]['id']
            logger.info("Single RPZ selected: %r", files[0]['name'])
            return 'figshare.com', '{0}/files/{1}'.format(article, file_id)

    async def get_experiment(self, db, object_store, remote_addr,
                             repo, repo_path):
        m = _figshare_path.match(repo_path)
        if m is None:
            raise RepositoryError("ID is not in Figshare format")
        article_id = m.group(1)
        file_id = m.group(2)
        logger.info("Querying Figshare for article=%s file=%s",
                    article_id, file_id)
        resp = await self.http_client.fetch(
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
            raise RepositoryError("HTTP error from Figshare")
        try:
            response = json.loads(resp.body.decode('utf-8'))
            link = response['download_url']
        except KeyError:
            raise RepositoryError("Invalid data returned from Figshare")
        except ValueError:
            logger.error("Got invalid JSON from Figshare")
            raise RepositoryError("Invalid JSON returned from Figshare")
        else:
            try:
                filename = response['name']
            except KeyError:
                filename = 'unnamed_figshare_file'
            logger.info("Got response: %s %s", link, filename)
            return await self._get_from_link(
                db, object_store, remote_addr,
                repo, repo_path,
                link, filename,
            )
