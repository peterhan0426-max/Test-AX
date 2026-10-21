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
from collector.authenticate import login_request, SafeRedirect
from collector.native import Client, read_limited
from collector.schedule import due_in
from datetime import datetime
from saenong.data import KST
from collector.run import collect_job
from collector.categories import sync_categories
from unittest.mock import patch


def report(net=100, discount=10, broken=False):
    headers = ['No', '소분류', '상품코드', '상품명', '순매출', '순매입', '기초재고', '기말재고', '할인액', '총할인']
    item = ['1', '테스트분류', 'P1', '<script>bad()</script>', str(net), '50', '20', '10', str(discount), '99']
    import html
    cells = lambda values: ''.join('<td>' + html.escape(value) + '</td>' for value in values)
    total = [str(net + int(broken)), '50', '20', '10', str(discount), '99']
    return ('<table><tr>' + cells(headers) + '</tr><tr>' + cells(item) +
            '</tr><tr><td colspan="4">전체 합계</td>' + cells(total) + '</tr></table>').encode('cp949')


def daily_report(gross, net, discount, day):
    rows = [['매출일자', '총매출', '순매출', '할인'], ['매출일자', '총매출', '순매출', '할인'],
            [day, str(gross), str(net), str(discount)], ['전체합계', str(gross), str(net), str(discount)]]
    return ('<table>' + ''.join('<tr>' + ''.join('<td>' + value + '</td>' for value in row) + '</tr>' for row in rows) + '</table>').encode('cp949')


class SalesTest(unittest.TestCase):
    def test_unreconciled_profit_is_withheld_and_target_uses_confirmed_revenue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monthly_file, daily_file = root / 'monthly.xls', root / 'daily.xls'
            monthly_file.write_bytes(report())
            daily_file.write_bytes(daily_report(120, 100, 20, '2025-03-01'))
            import_report(root, monthly_file, '가락', '2025-03', '2025-03-31', source_query={'check_yn': '5'})
            import_report(root, daily_file, '가락', '2025-03', '2025-03-31')
            view = dashboard(root, '2025-03')
            self.assertEqual(view['stores'][0]['gross'], 120)
            self.assertIsNone(view['stores'][0]['profit'])
            self.assertIsNone(view['stores'][0]['margin'])
            self.assertTrue(view['stores'][0]['revenue_only'])
            self.assertEqual(view['categories'], [])
            monthly_file.write_bytes(report(net=200))
            import_report(root, monthly_file, '가락', '2026-03', '2026-03-31', source_query={'check_yn': '5'})
            self.assertEqual(dashboard(root, '2026-03')['stores'][0]['sales_target'], 126)

    def test_retired_category_is_kept_in_other_without_guessing_ambiguous_labels(self):
        rows = [{'product': 'A', 'subcategory': '공통'}, {'product': 'B', 'subcategory': '공통'},
                {'product': 'C', 'subcategory': '냉동'}, {'product': 'D', 'subcategory': '옛분류'}]
        with tempfile.TemporaryDirectory() as directory:
            with patch('collector.categories.Client') as factory:
                factory.return_value.report.side_effect = [
                    (b'', {'rows': rows}), (b'', {'rows': rows[:1]}),
                    (b'', {'rows': rows[1:2]}), (b'', {'rows': rows[2:3]})]
                self.assertEqual(sync_categories(directory, '2024-01', ['가락']), 4)
            root = Path(directory)
            self.assertEqual(json.loads((root / 'product-categories.json').read_text())['D'], '기타')
            self.assertNotIn('공통', json.loads((root / 'categories.json').read_text()))

    def test_stream_heartbeat_does_not_keep_report_alive_forever(self):
        from unittest.mock import Mock
        response = Mock()
        response.read1.return_value = b' '
        with patch('collector.native.time.monotonic', side_effect=[0, 121]):
            with self.assertRaises(TimeoutError):
                read_limited(response)

    def test_repeated_network_failure_stops_and_preserves_verified_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'source.xls'
            file.write_bytes(report())
            import_report(root, file, '가락', '2026-10', '2026-10-21')
            with patch('collector.run.Client') as factory:
                factory.return_value.collect.side_effect = TimeoutError()
                run = collect_job(root, ['2026-10'], ['가락'],
                                  ['monthly-profit', 'daily-sales', 'promotion-summary'])
                self.assertEqual(factory.return_value.collect.call_count, 2)
            self.assertEqual(dashboard(root)['stores'][0]['gross'], 110)
            self.assertTrue(run['failures'])
            self.assertEqual(run['failures'][-1]['stage'], 'collection')

    def test_password_query_and_insecure_redirect_are_rejected(self):
        from urllib.request import Request
        request = Request('https://www2.atpos.co.kr/Login/CheckUser.php3')
        for target in ('http://www2.atpos.co.kr/Login/frame_set.php3',
                       'https://www2.atpos.co.kr/Login/frame_set.php3?passwd=synthetic'):
            with self.assertRaises(RuntimeError):
                SafeRedirect().redirect_request(request, None, 302, '', {}, target)

    def test_explicit_no_sales_is_zero_but_unexplained_blanks_fail(self):
        import html
        headers = ['매출일자', '총매출', '순매출', '할인', '영수건수']
        rows = [headers, headers, ['2024-01-01', '', '', '', '<< 해당 매출이 없습니다 >>'],
                ['2024-01-02', '100', '80', '20', '1'], ['전체합계', '100', '80', '20', '1']]
        content = '<table>' + ''.join('<tr>' + ''.join('<td>' + html.escape(v) + '</td>' for v in row) + '</tr>' for row in rows) + '</table>'
        result = read_report(content.encode('cp949'))
        self.assertEqual(result['rows'][0]['gross'], 0)
        self.assertTrue(result['rows'][0]['confirmed_no_sales'])
        with self.assertRaises(ValueError):
            read_report(content.replace(html.escape('<< 해당 매출이 없습니다 >>'), '').encode('cp949'))

    def test_daily_worker_avoids_duplicate_attempts_and_uses_korean_time(self):
        now = datetime(2026, 10, 21, 9, 0, tzinfo=KST)
        self.assertEqual(due_in(now, None, 8, 0), 0)
        self.assertEqual(due_in(now, '2026-10-21', 8, 0), 23 * 3600)
        before = datetime(2026, 10, 21, 7, 0, tzinfo=KST)
        self.assertEqual(due_in(before, '2026-10-20', 8, 0), 3600)

    def test_session_cookie_is_not_discarded_as_expired(self):
        from urllib.request import Request
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'atpos-session.json').write_text(json.dumps({'cookies': [{
                'name': 'PHPSESSID', 'value': 'synthetic-session', 'domain': 'www2.atpos.co.kr',
                'path': '/', 'secure': False, 'expires': -1}], 'origins': []}))
            client = Client(root)
            request = Request('https://www2.atpos.co.kr/Login/frame_set.php3')
            client.jar.add_cookie_header(request)
            self.assertEqual(request.get_header('Cookie'), 'PHPSESSID=synthetic-session')

    def test_inventory_display_difference_preserves_footer_and_rows(self):
        content = report().decode('cp949')
        before, after = content.split('<td colspan="4">')
        content = (before + '<td colspan="4">' + after.replace('<td>10</td>', '<td>11</td>', 1)).encode('cp949')
        result = read_report(content)
        self.assertEqual(result['totals']['closing'], 11)
        self.assertEqual(result['rows'][0]['closing'], 10)
        self.assertEqual(result['source_differences'], {'closing': 1})

    def test_auth_credentials_are_only_in_post_body(self):
        for stage in ('user', 'pass'):
            request = login_request('test-account', 'test-password', stage)
            self.assertEqual(request.get_method(), 'POST')
            self.assertNotIn('?', request.full_url)
            self.assertNotIn('test-password', request.full_url)
            self.assertIn(b'passwd=test-password', request.data)

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
