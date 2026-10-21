"""Exercise the source-account form without contacting ATPOS."""
import argparse
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from collector.credentials import load
from saenong.server import create_server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'ATPOS_ID': '', 'ATPOS_PASSWORD': '', 'SAENONG_AUTOCOLLECT': '1',
            'SAENONG_SOURCE_CONNECTED': '0'}), patch('collector.authenticate.establish_session') as authenticate:
        root = Path(folder) / 'data'
        root.mkdir()
        server = create_server(root, port=0, password='synthetic-viewer-password')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with sync_playwright() as playwright:
                options = {'headless': True}
                if args.executable:
                    options['executable_path'] = args.executable
                browser = playwright.chromium.launch(**options)
                try:
                    page = browser.new_page(http_credentials={'username': 'saenong', 'password': 'synthetic-viewer-password'})
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.wait_for_function("document.querySelector('#automation').textContent.includes('연결 대기')")
                    page.locator('#connect-source').click()
                    page.locator('#source-account').fill('synthetic-account')
                    page.locator('#source-password').fill('synthetic-password')
                    page.locator('#source-submit').click()
                    page.wait_for_function("document.querySelector('#source-message').textContent.includes('연결되었습니다')")
                    page.wait_for_function("document.querySelector('#automation').textContent.includes('매일 오전 5시')")
                    assert page.locator('#source-password').input_value() == ''
                    assert page.locator('#source-account').input_value() == ''
                    assert load(root) == ('synthetic-account', 'synthetic-password')
                    authenticate.assert_called_once()
                    print('PASS: source connection form, private storage, cleared fields and updated status')
                    print('ATPOS authentication mocked; no source-site requests made')
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    main()
