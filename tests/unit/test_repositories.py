import asyncio
import contextlib
import os
from tornado.testing import AsyncTestCase, gen_test
from tornado.util import ObjectDict
from unittest.mock import patch

from reproserver.repositories import get_experiment_from_repository, \
    parse_repository_url


def mock_fetch(self, expected_url, content):
    def fetch(s, url, **kwargs):
        self.assertEqual(url, expected_url)
        fut = asyncio.Future()
        response = ObjectDict()
        response.code = 200
        response.body = content
        fut.set_result(response)
        return fut

    return patch('tornado.httpclient.AsyncHTTPClient.fetch', fetch)


class TestParse(AsyncTestCase):
    @gen_test
    async def test_parse_osf(self):
        self.assertEqual(
            await parse_repository_url('https://osf.io/ab12c/download/'),
            ('osf.io', 'ab12c'),
        )
        self.assertEqual(
            await parse_repository_url('https://osf.io/ab12c/'),
            ('osf.io', 'ab12c'),
        )
        self.assertEqual(
            await parse_repository_url('https://osf.io/ab12c'),
            ('osf.io', 'ab12c'),
        )

    @gen_test
    async def test_parse_zenodo(self):
        os.environ['ZENODO_TOKEN'] = 'testtoken'
        mock_api = mock_fetch(
            self,
            'https://zenodo.org/api/deposit/depositions/1234567',
            b'''\
{"conceptdoi":"10.5281/zenodo.3374941","conceptrecid":"3374941","created":"201\
9-08-22T20:06:38.217019+00:00","doi":"10.5281/zenodo.1234567","doi_url":"https\
://doi.org/10.5281/zenodo.1234567","files":[{"checksum":"82ba6d51752a84e2d17d1\
18c9c5dde51","filename":"bash-count.rpz","filesize":1706841,"id":"98231160-432\
5-480d-847f-c0847f9c2b89","links":{"download":"https://zenodo.org/api/files/b5\
8e5c92-1c52-4415-90b7-5f73def827f2/bash-count.rpz","self":"https://zenodo.org/\
api/deposit/depositions/3379060/files/98231160-4325-480d-847f-c0847f9c2b89"}}]\
,"id":1234567,"links":{"badge":"https://zenodo.org/badge/doi/10.5281/zenodo.12\
34567.svg","bucket":"https://zenodo.org/api/files/b58e5c92-1c52-4415-90b7-5f73\
def827f2","conceptbadge":"https://zenodo.org/badge/doi/10.5281/zenodo.3374941.\
svg","conceptdoi":"https://doi.org/10.5281/zenodo.3374941","discard":"https://\
zenodo.org/api/deposit/depositions/1234567/actions/discard","doi":"https://doi\
.org/10.5281/zenodo.1234567","edit":"https://zenodo.org/api/deposit/deposition\
s/1234567/actions/edit","files":"https://zenodo.org/api/deposit/depositions/12\
34567/files","html":"https://zenodo.org/deposit/1234567","latest":"https://zen\
odo.org/api/records/1234567","latest_html":"https://zenodo.org/record/1234567"\
,"newversion":"https://zenodo.org/api/deposit/depositions/1234567/actions/newv\
ersion","publish":"https://zenodo.org/api/deposit/depositions/1234567/actions/\
publish","record":"https://zenodo.org/api/records/1234567","record_html":"http\
s://zenodo.org/record/1234567","registerconceptdoi":"https://zenodo.org/api/de\
posit/depositions/1234567/actions/registerconceptdoi","self":"https://zenodo.o\
rg/api/deposit/depositions/1234567"},"metadata":{"access_right":"open","commun\
ities":[{"identifier":"zenodo"}],"creators":[{"affiliation":"New York Universi\
ty","name":"Remi Rampin","orcid":"0000-0002-0524-2282"}],"description":"<p>An \
simple example of RPZ file for testing.</p>","doi":"10.5281/zenodo.1234567","k\
eywords":["rpz","reprozip","example","bash","word-count"],"license":"other-ope\
n","prereserve_doi":{"doi":"10.5281/zenodo.1234567","recid":1234567},"publicat\
ion_date":"2016-09-28","title":"ReproZip bash-count example","upload_type":"so\
ftware","version":"1.0"},"modified":"2019-08-22T20:07:04.520169+00:00","owner"\
:13329,"record_id":1234567,"state":"done","submitted":true,"title":"ReproZip b\
ash-count example"}\
            ''',
        )

        with mock_api:
            self.assertEqual(
                await parse_repository_url(
                    'https://zenodo.org/record/1234567/files/bash-count.rpz' +
                    '?download=1'
                ),
                ('zenodo.org', '1234567/files/bash-count.rpz'),
            )
            self.assertEqual(
                await parse_repository_url(
                    'https://zenodo.org/record/1234567/files/bash-count.rpz'
                ),
                ('zenodo.org', '1234567/files/bash-count.rpz'),
            )
            self.assertEqual(
                await parse_repository_url(
                    'https://zenodo.org/record/1234567/'
                ),
                ('zenodo.org', '1234567/files/bash-count.rpz'),
            )
            self.assertEqual(
                await parse_repository_url(
                    'https://zenodo.org/record/1234567'
                ),
                ('zenodo.org', '1234567/files/bash-count.rpz'),
            )

    @gen_test
    async def test_parse_figshare(self):
        mock_api = mock_fetch(
            self,
            'https://api.figshare.com/v2/articles/1234567/files',
            b'''\
[{"is_link_only": false, "name": "poster-force2018.rpz", "supplied_md5": "f2b4\
37fce5fe1a43b3499ea4915ec718", "computed_md5": "f2b437fce5fe1a43b3499ea4915ec7\
18", "id": 3456789, "download_url": "https://ndownloader.figshare.com/files/1\
3246142", "size": 374688}]\
            '''
        )

        with mock_api:
            self.assertEqual(
                await parse_repository_url(
                    'https://figshare.com/articles/ReproZip_Computational_'
                    'Reproducibility_With_Ease/1234567'
                ),
                ('figshare.com', '1234567/files/3456789')
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
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch(
                'reproserver.repositories.base.BaseRepository._get_from_link',
                TestGet.mock_get,
            ))
            mock_api = mock_fetch(
                self,
                'https://api.osf.io/v2/files/ab12c/',
                b'''\
{"data":{"relationships":{"metadata_records":{"links":{"related":{"href":"http\
s://api.osf.io/v2/files/57ec1614b83f6901ec94a940/metadata_records/","meta":{}}\
}},"node":{"data":{"type":"nodes","id":"8uxpv"},"links":{"related":{"href":"ht\
tps://api.osf.io/v2/nodes/8uxpv/","meta":{}}}},"target":{"links":{"related":{"\
href":"https://api.osf.io/v2/nodes/8uxpv/","meta":{"type":"node"}}}},"comments\
":{"links":{"related":{"href":"https://api.osf.io/v2/nodes/8uxpv/comments/?fil\
ter%5Btarget%5D=ab12c","meta":{}}}},"versions":{"links":{"related":{"href":"ht\
tps://api.osf.io/v2/files/57ec1614b83f6901ec94a940/versions/","meta":{}}}}},"l\
inks":{"info":"https://api.osf.io/v2/files/57ec1614b83f6901ec94a940/","render"\
:"https://mfr.osf.io/render?url=https://osf.io/download/ab12c/?direct%26mode=r\
ender","self":"https://api.osf.io/v2/files/57ec1614b83f6901ec94a940/","move":"\
https://files.osf.io/v1/resources/8uxpv/providers/osfstorage/57ec1614b83f6901e\
c94a940","upload":"https://files.osf.io/v1/resources/8uxpv/providers/osfstorag\
e/57ec1614b83f6901ec94a940","html":"https://osf.io/8uxpv/files/osfstorage/57ec\
1614b83f6901ec94a940","download":"https://osf.io/download/ab12c/","delete":"ht\
tps://files.osf.io/v1/resources/8uxpv/providers/osfstorage/57ec1614b83f6901ec9\
4a940"},"attributes":{"extra":{"hashes":{"sha256":"b1e2a821810fc927ed621d8441e\
bd65d6316a3f4477929d5565f2f2523e1344f","md5":"6f780275d8eca8d93e1149bd5f003538\
"},"downloads":257},"kind":"file","name":"digits_sklearn_opencv.rpz","last_tou\
ched":null,"materialized_path":"/digits_sklearn_opencv.rpz","date_modified":"2\
016-09-28T19:12:20.246000Z","current_version":1,"date_created":"2016-09-28T19:\
12:20.246000Z","provider":"osfstorage","path":"/57ec1614b83f6901ec94a940","cur\
rent_user_can_comment":false,"guid":"ab12c","checkout":null,"tags":[],"size":8\
2797083},"type":"files","id":"57ec1614b83f6901ec94a940"},"meta":{"version":"2.\
0"}}\
'''
            )
            stack.enter_context(mock_api)
            self.assertEqual(
                await get_experiment_from_repository(
                    self.db, self.object_store, '1.2.3.4',
                    'osf.io', 'ab12c',
                ),
                (self.result, 'https://osf.io/download/ab12c/'),
            )

    @gen_test
    async def test_get_zenodo(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch(
                'reproserver.repositories.base.BaseRepository._get_from_link',
                TestGet.mock_get,
            ))
            mock_api = mock_fetch(self, 'no_call', b'')
            stack.enter_context(mock_api)
            self.assertEqual(
                await get_experiment_from_repository(
                    self.db, self.object_store, '1.2.3.4',
                    'zenodo.org', '1234567/files/bash-count.rpz',
                ),
                (
                    self.result,
                    'https://zenodo.org/record/1234567/files/bash-count.rpz' +
                    '?download=1',
                ),
            )

    @gen_test
    async def test_get_figshare(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch(
                'reproserver.repositories.base.BaseRepository._get_from_link',
                TestGet.mock_get,
            ))
            mock_api = mock_fetch(
                self,
                'https://api.figshare.com/v2/articles/1234567/files/3456789',
                b'''\
{"is_link_only": false, "name": "poster-force2018.rpz", "supplied_md5": "f2b43\
7fce5fe1a43b3499ea4915ec718", "computed_md5": "f2b437fce5fe1a43b3499ea4915ec71\
8", "id": 3456789, "download_url": "https://ndownloader.figshare.com/files/345\
6789", "size": 374688}\
'''
            )
            stack.enter_context(mock_api)
            self.assertEqual(
                await get_experiment_from_repository(
                    self.db, self.object_store, '1.2.3.4',
                    'figshare.com', '1234567/files/3456789',
                ),
                (
                    self.result,
                    'https://ndownloader.figshare.com/files/3456789',
                ),
            )
