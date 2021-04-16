import os
import re
import requests
import tempfile
import time
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
            content = res.content.decode('utf-8')
            self.assertIn('Command-line for step run0', content)

            # Have it run
            m = re.search(r'/reproduce/([a-z0-9]{5})$', res.url)
            upload_short_id = m.group(1)
            res = self.reproserver_post(
                '/run/{0}'.format(upload_short_id),
                data={'param_cmdline_00000': './count.sh'},
            )

            # Get results
            m = re.search(r'/results/([a-z0-9]{5})$', res.url)
            run_short_id = m.group(1)
            obj = None
            for i in range(20):
                time.sleep(2)
                res = self.reproserver_get(
                    '/results/{0}?log_from=0'.format(run_short_id),
                    headers={'Accept': 'application/json'},
                )
                obj = res.json()
                if obj['done']:
                    break
            else:
                self.fail("Run didn't complete: %r" % (obj,))
            self.assertEqual(
                obj['log'],
                ['5', '*** Command finished, status: 0'],
            )
