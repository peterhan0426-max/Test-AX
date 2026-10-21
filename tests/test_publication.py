import json
import tempfile
import unittest
from pathlib import Path

from saenong.data import dashboard, import_report
from saenong.publication import publish, published_root, publication_status
from test_sales import report, daily_report


class PublicationTest(unittest.TestCase):
    def test_missing_daily_sales_keeps_previous_view_and_stale_profit_is_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.xls'
            source.write_bytes(report())
            import_report(root, source, '가락', '2026-10', '2026-10-20', source_query={'check_yn': '5'})
            source.write_bytes(daily_report(110, 100, 10, '2026-10-21'))
            import_report(root, source, '가락', '2026-10', '2026-10-21')
            publish(root, '2026-10-21', required_stores=['가락'])
            first = published_root(root)
            self.assertTrue(dashboard(first)['stores'][0]['revenue_only'])
            with self.assertRaises(ValueError):
                publish(root, '2026-10-22', required_stores=['가락'])
            self.assertEqual(published_root(root), first)
    def test_daily_view_stays_frozen_until_next_complete_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.xls'
            source.write_bytes(report(net=100))
            import_report(root, source, '가락', '2026-10', '2026-10-20', source_query={'check_yn': '5'})
            publish(root, '2026-10-20')
            first = published_root(root)
            self.assertEqual(dashboard(first)['stores'][0]['gross'], 110)
            source.write_bytes(report(net=200))
            import_report(root, source, '가락', '2026-10', '2026-10-21', source_query={'check_yn': '5'})
            self.assertEqual(dashboard(published_root(root))['stores'][0]['gross'], 110)
            self.assertEqual(dashboard(root)['stores'][0]['gross'], 210)
            with self.assertRaises(ValueError):
                publish(root, '2026-10-20')
            self.assertEqual(published_root(root), first)
            publish(root, '2026-10-21')
            self.assertEqual(dashboard(published_root(root))['stores'][0]['gross'], 210)
            self.assertEqual(publication_status(root)['cutoff'], '2026-10-21')

    def test_backup_restores_published_view_without_exposing_unpublished_changes(self):
        from saenong.backup import create_backup, restore_backup
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / 'data'
            root.mkdir()
            source = base / 'source.xls'
            source.write_bytes(report(net=100))
            import_report(root, source, '가락', '2026-10', '2026-10-20')
            publish(root, '2026-10-20')
            source.write_bytes(report(net=200))
            import_report(root, source, '가락', '2026-10', '2026-10-21')
            archive = create_backup(root, base / 'backups')
            restored = base / 'restored'
            restore_backup(archive, restored)
            self.assertEqual(dashboard(restored)['stores'][0]['gross'], 210)
            self.assertEqual(dashboard(published_root(restored))['stores'][0]['gross'], 110)
            self.assertEqual(publication_status(restored)['cutoff'], '2026-10-20')

    def test_publication_never_exposes_source_account_or_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.xls'
            source.write_bytes(report())
            import_report(root, source, '가락', '2026-10', '2026-10-21')
            (root / 'atpos-session.json').write_text('{"secret":"DO_NOT_EXPORT"}')
            (root / 'categories.json').write_text('{}')
            publish(root, '2026-10-21')
            target = published_root(root)
            self.assertFalse((target / 'atpos-session.json').exists())
            self.assertEqual(json.loads((target / 'categories.json').read_text()), {})
