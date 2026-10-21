"""Exercise automatic refresh after a corrected report, without waiting five minutes."""
import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from saenong.data import import_report
from saenong.server import create_server


def seed(root, period, amount):
    fields = ['No', '소분류', '상품코드', '상품명', '순매출', '순매입', '기초재고', '기말재고', '할인액']
    values = [str(amount), '50', '20', '10', '10']
    cells = lambda row: ''.join('<td>' + value + '</td>' for value in row)
    content = '<table><tr>' + cells(fields) + '</tr><tr>' + cells(['1', '테스트', 'P1', '테스트 상품'] + values) + '</tr><tr><td colspan="4">전체 합계</td>' + cells(values) + '</tr></table>'
    file = root / 'source.xls'
    file.write_bytes(content.encode('cp949'))
    import_report(root, file, '가락', period, period + '-21', source_query={'check_yn': '5'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        seed(root, '2025-10', 100)
        seed(root, '2026-10', 500)
        server = create_server(root, port=0, password='synthetic-viewer-password')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                options = {'headless': True}
                if args.executable:
                    options['executable_path'] = args.executable
                browser = playwright.chromium.launch(**options)
                try:
                    context = browser.new_context(http_credentials={'username': 'saenong', 'password': 'synthetic-viewer-password'})
                    page = context.new_page()
                    page.clock.install()
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.wait_for_function("document.querySelectorAll('#cards .card').length===3")
                    page.locator('#period').select_option('2025-10')
                    page.wait_for_function("currentData?.period==='2025-10' && currentData.stores[0].gross===110")
                    seed(root, '2025-10', 200)
                    seed(root, '2026-11', 700)
                    page.clock.fast_forward(300001)
                    page.wait_for_function("currentData?.period==='2025-10' && currentData.stores[0].gross===210")
                    assert page.locator('#period').input_value() == '2025-10'
                    assert page.locator('#period option[value="2026-11"]').count() == 1
                    page.route('**/api/dashboard*', lambda route: route.fulfill(status=503, body='Unavailable'))
                    page.clock.fast_forward(300001)
                    page.wait_for_function("document.getElementById('status').textContent.includes('마지막 표시 자료')")
                    assert page.evaluate('currentData.stores[0].gross') == 210
                    assert '210원' in page.locator('#cards').inner_text()
                    page.unroute('**/api/dashboard*')
                    seed(root, '2025-10', 300)
                    page.clock.fast_forward(300001)
                    page.wait_for_function('currentData?.stores[0].gross===310')
                    assert page.locator('#status').inner_text() == ''
                    print('PASS: authenticated auto-refresh, preserved month, new months, failed-refresh data retention and recovery')
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
