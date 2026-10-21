import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from saenong.data import connect, dashboard, financials, import_report, sales_target
from saenong.reports import read_report
from saenong.server import create_server


def report(net=100, discount=10, broken=False):
    headers = ['No', '소분류', '상품코드', '상품명', '순매출', '순매입', '기초재고', '기말재고', '할인액', '총할인']
    item = ['1', '테스트분류', 'P1', '<script>bad()</script>', str(net), '50', '20', '10', str(discount), '99']
    import html
    cells = lambda values: ''.join('<td>' + html.escape(value) + '</td>' for value in values)
    total = [str(net + int(broken)), '50', '20', '10', str(discount), '99']
    return ('<table><tr>' + cells(headers) + '</tr><tr>' + cells(item) +
            '</tr><tr><td colspan="4">전체 합계</td>' + cells(total) + '</tr></table>').encode('cp949')


class SalesTest(unittest.TestCase):
    def test_discount_field_and_profit_formula(self):
        parsed = read_report(report())
        self.assertEqual(parsed['totals']['discount'], 10)
        result = financials(parsed['totals'])
        self.assertEqual((result['gross'], result['cogs'], result['profit']), (110, 60, 50))
        self.assertAlmostEqual(result['margin'], 50 / 110 * 100)
        self.assertIsNone(financials(dict(net=0, discount=0, purchases=0, opening=0, closing=0))['margin'])

    def test_footer_mismatch_and_login_page_fail(self):
        for content in (report(broken=True), b'<html>please login</html>'):
            with self.assertRaises(ValueError):
                read_report(content)

    def test_targets_average_growth_and_missing_baseline(self):
        history = {('가락', '2025-09'): {'gross': 100, 'as_of': '2025-09-30'},
                   ('가락', '2025-10'): {'gross': 300, 'as_of': '2025-10-31'},
                   ('동천', '2025-09'): {'gross': 100, 'as_of': '2025-09-30'},
                   ('동천', '2025-10'): {'gross': 300, 'as_of': '2025-10-31'}}
        self.assertEqual(sales_target('가락', '2026-09', history), 210)
        self.assertEqual(sales_target('가락', '2026-10', history), 210)
        self.assertEqual(sales_target('동천', '2026-10', history), 220)
        history[('가락', '2025-10')]['as_of'] = '2025-10-21'
        self.assertIsNone(sales_target('가락', '2026-09', history))
        self.assertIsNone(sales_target('야탑', '2026-10', history))

    def test_import_replaces_snapshot_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'test.xls'
            file.write_bytes(report())
            first = import_report(root, file, '가락', '2026-10', '2026-10-21')
            second = import_report(root, file, '가락', '2026-10', '2026-10-21')
            self.assertFalse(first['duplicate'])
            self.assertTrue(second['duplicate'])
            file.write_bytes(report(net=200))
            import_report(root, file, '가락', '2026-10', '2026-10-21')
            view = dashboard(root, '2026-10')
            self.assertEqual(view['stores'][0]['gross'], 210)
            self.assertTrue(view['stores'][1]['missing'])
            self.assertEqual(view['categories'][0]['category'], '미분류')
            self.assertIsNone(view['stores'][0]['sales_target'])
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 2)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM active').fetchone()[0], 1)
            with self.assertRaises(ValueError):
                import_report(root, file, '가락', '2026-10', '2026-10-20')
            file.write_bytes(report(broken=True))
            with self.assertRaises(ValueError):
                import_report(root, file, '가락', '2026-10', '2026-10-22')
            self.assertEqual(dashboard(root)['stores'][0]['gross'], 210)

    def test_promotion_negative_and_privacy_whitelist(self):
        headers = ['프로모션명', '고객번호', '카드번호', '고객명', '매출일자', '영수증번호',
                   '매장코드', '매장명', '상품코드', '상품명', '판매가', '매출수량', '총매출액', '순매출액', '할인금액']
        row = ['농할', 'PRIVATE_ID', 'PRIVATE_CARD', 'PRIVATE_NAME', '2026-10-20', 'PRIVATE_RECEIPT',
               'STORE', '새농푸드마켓(가락)', 'P1', '사과', '100', '-1', '-100', '-80', '-20']
        cells = lambda values: ''.join('<td>' + value + '</td>' for value in values)
        content = ('<table><tr>' + cells(headers) + '</tr><tr>' + cells(row) +
                   '</tr><tr><td colspan="11">전체합계</td>' + cells(['-1', '-100', '-80', '-20']) + '</tr></table>').encode('cp949')
        parsed = read_report(content)
        self.assertEqual(parsed['totals']['discount'], -20)
        self.assertNotIn('PRIVATE_', json.dumps(parsed))

    def test_server_serves_dashboard_and_refuses_raw_files(self):
        with tempfile.TemporaryDirectory() as directory:
            server = create_server(directory, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base + '/') as response:
                    self.assertIn('새농'.encode(), response.read())
                with urlopen(base + '/api/dashboard?period=2026-10') as response:
                    data = json.load(response)
                self.assertTrue(all(item['missing'] for item in data['stores']))
                for path, status in [('/private-originals/a.xls', 404), ('/sales.sqlite3', 404),
                                     ('/api/dashboard?period=invalid', 400)]:
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + path)
                    self.assertEqual(error.exception.code, status)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
