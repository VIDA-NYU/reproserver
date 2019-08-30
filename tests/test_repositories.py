import asyncio
from tornado.testing import AsyncTestCase, gen_test
from unittest.mock import patch

from reproserver.repositories import get_experiment_from_repository, \
    parse_repository_url


class TestParse(AsyncTestCase):
    @gen_test
    async def test_parse_osf(self):
        self.assertEqual(
            await parse_repository_url('https://osf.io/5ztp2/download/'),
            ('osf.io', '5ztp2'),
        )
        self.assertEqual(
            await parse_repository_url('https://osf.io/5ztp2/'),
            ('osf.io', '5ztp2'),
        )
        self.assertEqual(
            await parse_repository_url('https://osf.io/5ztp2'),
            ('osf.io', '5ztp2'),
        )

    @gen_test
    async def test_parse_zenodo(self):
        self.assertEqual(
            await parse_repository_url(
                'https://zenodo.org/record/3374942/files/bash-count.rpz' +
                '?download=1'
            ),
            ('zenodo.org', '3374942/files/bash-count.rpz'),
        )
        self.assertEqual(
            await parse_repository_url(
                'https://zenodo.org/record/3374942/files/bash-count.rpz'
            ),
            ('zenodo.org', '3374942/files/bash-count.rpz'),
        )
        self.assertEqual(
            await parse_repository_url(
                'https://zenodo.org/record/3374942/'
            ),
            ('zenodo.org', '3374942/files/bash-count.rpz'),
        )
        self.assertEqual(
            await parse_repository_url(
                'https://zenodo.org/record/3374942'
            ),
            ('zenodo.org', '3374942/files/bash-count.rpz'),
        )


class TestGet(AsyncTestCase):
    db = object()
    object_store = object()
    result = object()

    @staticmethod
    def mock_get(self, db, object_store, remote_addr, repo, repo_path,
                 link, filename, filehash=None):
        assert db is TestGet.db
        assert object_store is TestGet.object_store
        assert remote_addr == '1.2.3.4'
        future = asyncio.Future()
        future.set_result((TestGet.result, link))
        return future

    @gen_test
    async def test_get_osf(self):
        with patch(
            'reproserver.repositories.base.BaseRepository._get_from_link',
            TestGet.mock_get,
        ):
            self.assertEqual(
                await get_experiment_from_repository(
                    self.db, self.object_store, '1.2.3.4',
                    'osf.io', '5ztp2',
                ),
                (self.result, 'https://osf.io/download/5ztp2/'),
            )

    @gen_test
    async def test_get_zenodo(self):
        with patch(
            'reproserver.repositories.base.BaseRepository._get_from_link',
            TestGet.mock_get,
        ):
            self.assertEqual(
                await get_experiment_from_repository(
                    self.db, self.object_store, '1.2.3.4',
                    'zenodo.org', '3374942/files/bash-count.rpz',
                ),
                (
                    self.result,
                    'https://zenodo.org/record/3374942/files/bash-count.rpz' +
                    '?download=1',
                ),
            )
