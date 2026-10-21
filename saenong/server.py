"""Read-only HTTP service. Raw files and database are never served."""
import argparse
import base64
import binascii
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .data import dashboard


def operational_status(root):
    root = Path(root)

    def metadata(path):
        try:
            value = json.loads(path.read_text('utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    collection = metadata(root / 'collection-status.json')
    backup_dir = root.parent / 'backups'
    backup = metadata(backup_dir / 'backup-status.json')
    name = backup.get('file', '')
    valid_name = isinstance(name, str) and name.startswith('saenong-') and name.endswith('.tar.gz') and Path(name).name == name
    available = bool(valid_name and (backup_dir / name).is_file() and not (backup_dir / name).is_symlink())
    return {'collection': {'enabled': os.environ.get('SAENONG_AUTOCOLLECT') == '1',
                           'hour_kst': 8, 'last_finished_at': collection.get('finished_at'),
                           'failure_count': len(collection.get('failures', []))},
            'backup': {'available': available,
                       'last_completed_at': backup.get('finished_at') if available else None}}


def create_server(root, host='127.0.0.1', port=8000, password=None, username='saenong'):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if password is not None and url.path != '/health':
                scheme, _, value = self.headers.get('Authorization', '').partition(' ')
                try:
                    supplied = base64.b64decode(value, validate=True) if scheme.lower() == 'basic' else b''
                except (ValueError, binascii.Error):
                    supplied = b''
                if not hmac.compare_digest(supplied, (username + ':' + password).encode('utf-8')):
                    self.send_response(401)
                    self.send_header('WWW-Authenticate', 'Basic realm="Saenong", charset="UTF-8"')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
            if url.path == '/':
                body = Path(__file__).with_name('index.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
            elif url.path == '/api/dashboard':
                query = parse_qs(url.query)
                try:
                    data = dashboard(root, query.get('period', [None])[0])
                    body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
                except (ValueError, KeyError):
                    self.send_error(400, 'Invalid period or category configuration')
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
            elif url.path == '/api/status':
                body = json.dumps(operational_status(root), ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
            elif url.path == '/health':
                try:
                    dashboard(root)
                except Exception:
                    self.send_error(503, 'Database unavailable')
                    return
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
            else:
                self.send_error(404)
                return
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description='새농 매출 대시보드')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    password = os.environ.get('SAENONG_VIEWER_PASSWORD')
    if os.environ.get('SAENONG_REQUIRE_AUTH') == '1' and not password:
        parser.error('개인용 화면의 접속 암호 설정이 필요합니다.')
    server = create_server(args.data_dir, args.host, args.port, password,
                           os.environ.get('SAENONG_VIEWER_USER', 'saenong'))
    print('새농 조회 서버 시작. 종료: Ctrl+C')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
