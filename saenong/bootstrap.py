"""One-time, authenticated restore into an empty hosted data directory."""
import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .backup import restore_backup


def create_server(root, password, token, digest, host='127.0.0.1', port=8000):
    if len(password) < 20 or len(token) < 32 or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid bootstrap configuration')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    marker = root.parent / '.bootstrap-complete'
    restore_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value):
            body = json.dumps(value).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            if status == 401:
                self.send_header('WWW-Authenticate', 'Basic realm="Saenong"')
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def authorized(self):
            scheme, _, value = self.headers.get('Authorization', '').partition(' ')
            try:
                supplied = base64.b64decode(value, validate=True) if scheme.lower() == 'basic' else b''
            except (ValueError, binascii.Error):
                supplied = b''
            return (hmac.compare_digest(supplied, ('saenong:' + password).encode()) and
                    hmac.compare_digest(self.headers.get('X-Saenong-Bootstrap', '').encode(), token.encode()))

        def do_GET(self):
            if self.path == '/health':
                self.reply(200, {'status': 'ok'})
            elif not self.authorized():
                self.reply(401, {'status': 'unauthorized'})
            elif self.path == '/_bootstrap':
                self.reply(200, {'status': 'restored' if marker.exists() else 'awaiting_restore'})
            else:
                self.reply(404, {'status': 'unavailable'})

        def do_POST(self):
            if not self.authorized():
                self.reply(401, {'status': 'unauthorized'})
                return
            if self.path != '/_bootstrap':
                self.reply(404, {'status': 'unavailable'})
                return
            if not restore_lock.acquire(blocking=False):
                self.reply(409, {'status': 'restore_in_progress'})
                return
            try:
                self.restore()
            finally:
                restore_lock.release()

        def restore(self):
            if marker.exists() or any(root.iterdir()):
                self.reply(409, {'status': 'data_already_exists'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if (not 0 < length <= 100 * 1024 * 1024 or self.headers.get('Transfer-Encoding') or
                    self.headers.get('Content-Type') != 'application/octet-stream'):
                self.reply(400, {'status': 'invalid_upload'})
                return
            try:
                with tempfile.TemporaryDirectory(dir=root.parent) as folder:
                    archive = Path(folder) / 'restore.tar.gz'
                    fingerprint = hashlib.sha256()
                    remaining, deadline = length, time.monotonic() + 120
                    with archive.open('xb') as target:
                        archive.chmod(0o600)
                        while remaining:
                            left = deadline - time.monotonic()
                            if left <= 0:
                                raise TimeoutError('Upload deadline')
                            self.connection.settimeout(min(5, left))
                            chunk = self.rfile.read1(min(65536, remaining))
                            if not chunk:
                                raise ValueError('Incomplete upload')
                            target.write(chunk)
                            fingerprint.update(chunk)
                            remaining -= len(chunk)
                    if not hmac.compare_digest(fingerprint.hexdigest(), digest):
                        raise ValueError('Backup digest mismatch')
                    restore_backup(archive, root)
                    marker.write_text(digest, encoding='ascii')
                    marker.chmod(0o600)
                self.reply(200, {'status': 'restored'})
            except Exception:
                self.reply(400, {'status': 'restore_rejected'})

    # The lock prevents concurrent restores while health checks remain responsive.
    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if os.environ.get('SAENONG_AUTOCOLLECT') != '0':
        parser.error('Collection must be disabled during migration')
    server = create_server(args.data_dir, os.environ.get('SAENONG_VIEWER_PASSWORD', ''),
                           os.environ.get('SAENONG_BOOTSTRAP_TOKEN', ''),
                           os.environ.get('SAENONG_BOOTSTRAP_SHA256', ''), args.host, args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
