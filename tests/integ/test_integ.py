import os
import re
import requests
import tempfile
import unittest


class ReproserverTest(unittest.TestCase):
    xsrf = None

    _re_xsrf = re.compile(
        br'<input type="hidden" name="_xsrf" value="([^">]+)" */?>')

    def setUp(self):
        super(ReproserverTest, self).setUp()
        self.http = requests.session()

    def reproserver_get(self, url, **kwargs):
        return self._request('get', url, **kwargs)

    def reproserver_post(self, url, **kwargs):
        if self.xsrf is not None:
            data = kwargs.setdefault('data', {})
            data['_xsrf'] = self.xsrf
        return self._request('post', url, **kwargs)

    def _request(self, method, url, check_status=True, **kwargs):
        response = self.http.request(
            method,
            os.environ['WEB_URL'] + url,
            **kwargs,
        )
        if check_status:
            response.raise_for_status()
        if response.status_code < 400:
            m = self._re_xsrf.search(response.content)
            if m is not None:
                self.xsrf = m.group(1).decode('utf-8')
        return response


class TestRepro(ReproserverTest):
    def test_bash_default(self):
        # Get index
        res = self.reproserver_get('/')
        self.assertIsNotNone(self.xsrf)
        self.assertIn(b'Select a package to upload', res.content)

        with tempfile.TemporaryFile() as tmp:
            # Download the example file
            res = requests.get(
                'https://osf.io/5ke97/download',
                allow_redirects=True,
                stream=True,
            )
            res.raise_for_status()
            for chunk in res.iter_content(chunk_size=4096):
                tmp.write(chunk)
            tmp.flush()
            tmp.seek(0, 0)

            # Post the file
            res = self.reproserver_post('/upload', files={'rpz_file': tmp})
