import os
import re
import requests
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

        # Post the file
        with open('testdata/bash-count.rpz', 'rb') as fp:
            res = self.reproserver_post('/upload', files={'rpz_file': fp})
