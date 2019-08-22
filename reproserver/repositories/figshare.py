import json
import logging
from tornado.httpclient import AsyncHTTPClient

from .. import __version__
from .base import BaseRepository, RepositoryError


logger = logging.getLogger(__name__)


class Figshare(BaseRepository):
    IDENTIFIER = 'figshare.com'
    URL_DOMAINS = ['figshare.com']

    async def parse_url(self, url):
        if url.startswith('http://'):
            url = url[7:]
        elif url.startswith('https://'):
            url = url[8:]
        else:
            raise RepositoryError("Invalid URL")
        if url.lower().startswith('figshare.com/'):
            raise RepositoryError("Not Figshare URL")

        # TODO: Actually need to find the article ID, which is not in the URL?
        raise NotImplementedError

    async def get_experiment(self, db, object_store, remote_addr,
                             repo, repo_path):
        # article_id/file_id
        try:
            article_id, file_id = repo_path.split('/', 1)
            article_id = int(article_id)
            file_id = int(file_id)
        except ValueError:
            raise RepositoryError("ID is not in 'article_id/file_id' format")
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
