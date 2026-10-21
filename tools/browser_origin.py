"""A temporary loopback origin with no filesystem or application-data access."""
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer


@contextmanager
def crypto_origin():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/':
                self.send_error(404)
                return
            body = b'<!doctype html><title>Local browser check</title>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
