"""Read-only HTTP service. Raw files and database are never served."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .data import dashboard


def create_server(root, host='127.0.0.1', port=8000):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
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
    server = create_server(args.data_dir, args.host, args.port)
    print('새농 조회 서버 시작. 종료: Ctrl+C')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
