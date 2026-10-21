import base64
import hashlib
import io
import json
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from saenong.backup import create_backup, restore_backup
from saenong.bootstrap import create_server as bootstrap_server
from saenong.data import dashboard, import_report
from saenong.server import create_server


def seed(root, net=100):
    headers = ['No', '소분류', '상품코드', '상품명', '순매출', '순매입', '기초재고', '기말재고', '할인액']
    values = [str(net), '50', '20', '10', '10']
    cells = lambda row: ''.join('<td>' + value + '</td>' for value in row)
    content = ('<table><tr>' + cells(headers) + '</tr><tr>' +
               cells(['1', '테스트', 'P1', '테스트 상품'] + values) +
               '</tr><tr><td colspan="4">전체 합계</td>' + cells(values) + '</tr></table>')
    path = root / 'source.xls'
    path.write_bytes(content.encode('cp949'))
    import_report(root, path, '가락', '2026-10', '2026-10-21', source_query={'check_yn': '5'})


class OperationsTest(unittest.TestCase):
    def check_bootstrap(self, archive, root, invalid=False):
        content = archive.read_bytes()
        password, token = 'synthetic-viewer-password', 'synthetic-bootstrap-token-at-least-32'
        server = bootstrap_server(root, password, token, hashlib.sha256(content).hexdigest(), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        headers = {'Authorization': 'Basic ' + base64.b64encode(('saenong:' + password).encode()).decode(),
                   'X-Saenong-Bootstrap': token, 'Content-Type': 'application/octet-stream'}

        def post(value, expected, extra=None):
            request = Request(base + '/_bootstrap', data=value, headers={**headers, **(extra or {})})
            try:
                with urlopen(request) as response:
                    self.assertEqual(response.status, expected)
                    return json.load(response)
            except HTTPError as error:
                self.assertEqual(error.code, expected)

        try:
            with urlopen(base + '/health') as response:
                self.assertEqual(json.load(response), {'status': 'ok'})
            post(content, 401, {'X-Saenong-Bootstrap': 'incorrect-token'})
            post(content, 401, {'Authorization': 'Basic ???'})
            post(content + b'wrong', 400)
            self.assertFalse(any(root.iterdir()))
            if invalid:
                post(content, 400)
                self.assertFalse(any(root.iterdir()))
                self.assertFalse((root.parent / 'escape').exists())
            else:
                self.assertEqual(post(content, 200), {'status': 'restored'})
                self.assertEqual(dashboard(root)['stores'][0]['gross'], 110)
                post(content, 409)
                with urlopen(Request(base + '/_bootstrap', headers=headers)) as response:
                    self.assertEqual(json.load(response), {'status': 'restored'})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_hosted_bootstrap_auth_digest_restore_and_replay_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / 'data'
            data.mkdir()
            seed(data)
            archive = create_backup(data, root / 'backups')
            self.check_bootstrap(archive, root / 'restored')

    def test_hosted_bootstrap_rejects_unsafe_archive_without_writing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'bad.tar.gz'
            with tarfile.open(archive, 'w:gz') as stream:
                for name in ('sales.sqlite3', '../escape'):
                    member = tarfile.TarInfo(name)
                    member.size = 3
                    stream.addfile(member, io.BytesIO(b'bad'))
            self.check_bootstrap(archive, root / 'restored', invalid=True)

    def test_private_api_requires_auth_but_health_reveals_no_financial_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seed(root)
            password = 'synthetic-viewer-password'
            server = create_server(root, port=0, password=password)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base + '/health') as response:
                    self.assertEqual(json.load(response), {'status': 'ok'})
                for value in ('', 'Basic ???', 'Basic ' + base64.b64encode(b'saenong:wrong').decode()):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(base + '/api/dashboard', headers={'Authorization': value}))
                    self.assertEqual(error.exception.code, 401)
                authorization = 'Basic ' + base64.b64encode(('saenong:' + password).encode()).decode()
                with urlopen(Request(base + '/api/dashboard', headers={'Authorization': authorization})) as response:
                    self.assertEqual(json.load(response)['stores'][0]['gross'], 110)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_backup_restores_history_originals_and_totals_without_login_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, backups, restored = root / 'data', root / 'backups', root / 'restored'
            data.mkdir()
            seed(data)
            seed(data, net=200)
            (data / 'atpos-session.json').write_text('{"cookie":"DO_NOT_EXPORT"}')
            archive = create_backup(data, backups, keep=1)
            with tarfile.open(archive) as stream:
                self.assertNotIn('atpos-session.json', stream.getnames())
                self.assertEqual(len([member for member in stream if member.name.startswith('private-originals/')]), 2)
            restore_backup(archive, restored)
            self.assertEqual(dashboard(restored)['stores'], dashboard(data)['stores'])
            with self.assertRaises(ValueError):
                restore_backup(archive, restored)
            latest = create_backup(data, backups, keep=1)
            self.assertFalse(archive.exists())
            next((data / 'private-originals').glob('*.xls')).unlink()
            with self.assertRaises(ValueError):
                create_backup(data, backups, keep=1)
            self.assertTrue(latest.exists())

    def test_restore_rejects_traversal_before_extracting_any_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive, restored = root / 'bad.tar.gz', root / 'restored'
            with tarfile.open(archive, 'w:gz') as stream:
                for name in ('sales.sqlite3', '../escape'):
                    member = tarfile.TarInfo(name)
                    member.size = 3
                    stream.addfile(member, io.BytesIO(b'bad'))
            with self.assertRaises(ValueError):
                restore_backup(archive, restored)
            self.assertFalse((root / 'escape').exists())
            self.assertFalse(any(restored.iterdir()))
