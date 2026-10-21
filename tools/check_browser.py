"""Local browser integration test using synthetic data; no ATPOS credentials."""
import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from saenong.data import dashboard
from saenong.server import create_server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--executable')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = args.data_dir or Path(temporary)
        server = create_server(root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                options = {'headless': True}
                if args.executable:
                    options['executable_path'] = args.executable
                browser = playwright.chromium.launch(**options)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    response = page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='networkidle')
                    assert response.ok
                    page.wait_for_function("document.querySelectorAll('#cards .card').length === 3")
                    view = dashboard(root)
                    for item in view['stores']:
                        if not item.get('missing'):
                            assert f"{item['gross']:,}원" in page.locator('#cards').inner_text()
                    assert page.locator('#sources tbody tr').count() == len(view['sources'])
                    for width in (1440, 390):
                        page.set_viewport_size({'width': width, 'height': 900})
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    options = page.locator('#trend-category option').count()
                    if options > 1:
                        page.locator('#trend-category').select_option(index=1)
                        assert '매출 추이' in page.locator('#trend-title').inner_text()
                    # Also exercise a missing month instead of silently retaining the previous cards.
                    result = page.evaluate("fetch('/api/dashboard?period=2024-01').then(r=>r.json())")
                    assert result['period'] == '2024-01'
                    assert not errors, errors
                    print('PASS: HTTP, browser rendering, mobile layout and API integration')
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
