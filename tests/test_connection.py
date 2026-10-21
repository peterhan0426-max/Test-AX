import base64
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from collector import credentials, schedule
from saenong.data import KST
from saenong.server import create_server


class ConnectionTest(unittest.TestCase):
    def test_validated_account_is_private_and_failed_replacement_preserves_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'data'
            root.mkdir()
            with patch('collector.authenticate.establish_session') as authenticate:
                credentials.connect(root, ' synthetic-account ', 'synthetic-password')
                self.assertEqual(authenticate.call_args.kwargs['account'], 'synthetic-account')
            self.assertEqual(credentials.load(root), ('synthetic-account', 'synthetic-password'))
            if os.name != 'nt':
                self.assertEqual(credentials.credential_path(root).stat().st_mode & 0o077, 0)
            with patch('collector.authenticate.establish_session', side_effect=RuntimeError('DO_NOT_DISPLAY')):
                with self.assertRaises(RuntimeError):
                    credentials.connect(root, 'different-account', 'invalid-password')
            self.assertEqual(credentials.load(root), ('synthetic-account', 'synthetic-password'))
            self.assertFalse((root / 'credentials').exists())

    def test_connection_requires_viewer_auth_same_origin_and_validated_login(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'data'
            root.mkdir()
            password = 'synthetic-viewer-password'
            server = create_server(root, port=0, password=password)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            headers = {'Origin': base, 'Content-Type': 'application/json',
                       'Authorization': 'Basic ' + base64.b64encode(('saenong:' + password).encode()).decode()}

            def post(extra, expected):
                body = json.dumps({'account': 'synthetic-account', 'password': 'synthetic-password'}).encode()
                req = Request(base + '/api/source-connection', data=body, headers={**headers, **extra})
                try:
                    with urlopen(req) as response:
                        self.assertEqual(response.status, expected)
                        return json.load(response)
                except HTTPError as error:
                    self.assertEqual(error.code, expected)

            try:
                with patch.dict(os.environ, {'SAENONG_SITE_ORIGIN': base}), patch('collector.authenticate.establish_session') as authenticate:
                    post({'Authorization': ''}, 401)
                    post({'Origin': 'https://untrusted.example'}, 403)
                    post({'Content-Type': 'application/x-www-form-urlencoded'}, 400)
                    authenticate.assert_not_called()
                    self.assertEqual(post({}, 200), {'connected': True})
                    post({}, 429)
                    authenticate.assert_called_once()
                with urlopen(Request(base + '/api/status', headers=headers)) as response:
                    state = response.read().decode()
                    self.assertTrue(json.loads(state)['collection']['connected'])
                    self.assertNotIn('synthetic-password', state)
                self.assertEqual(credentials.load(root), ('synthetic-account', 'synthetic-password'))
            finally:
                server.shutdown()
                server.server_close()
                worker.join()

    def test_scheduler_waits_for_account_and_attempts_once_per_new_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime(2026, 10, 21, 9, tzinfo=KST)
            with patch('sys.argv', ['schedule', '--data-dir', folder]), \
                    patch('collector.schedule.datetime') as clock, \
                    patch('collector.schedule.time.sleep'), \
                    patch('collector.schedule.version', side_effect=[None, 'v1', 'v1', 'v2', KeyboardInterrupt]), \
                    patch('collector.schedule.collect_job', return_value={'failures': []}) as collect:
                clock.now.return_value = now
                schedule.main()
            self.assertEqual(collect.call_count, 2)
            marker = json.loads((Path(folder) / 'scheduled-last-attempt.json').read_text())
            self.assertEqual(marker['credential_version'], 'v2')
            self.assertEqual(marker['date'], '2026-10-21')
