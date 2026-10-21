import json
import unittest
from unittest.mock import patch

from saenong.offline import render, snapshot


class OfflineTest(unittest.TestCase):
    def test_untrusted_labels_cannot_close_embedded_json_script(self):
        label = '</script><script>window.untrusted=true</script>&'
        bundle = {'version': 1, 'initial_period': '2026-10',
                  'views': {'2026-10': {'warnings': [label]}}}
        document = render(bundle)
        self.assertNotIn(label, document)
        start = '<script id="saenong-snapshot" type="application/json">'
        embedded = document.split(start, 1)[1].split('</script>', 1)[0]
        self.assertEqual(json.loads(embedded), bundle)

    def test_all_months_keep_metrics_but_job_diagnostics_are_not_exported(self):
        current = {'period': '2026-10', 'periods': ['2026-10', '2025-10'],
                   'stores': [{'store': '가락', 'gross': 110, 'profit': None}],
                   'collection': {'finished_at': '2026-10-21T08:00:00+09:00',
                                  'failures': [{'error': 'private diagnostic'}]}}
        previous = {'period': '2025-10', 'stores': [{'store': '가락', 'gross': 100}],
                    'collection': None}
        with patch('saenong.offline.dashboard', side_effect=[current, previous]):
            bundle = snapshot('unused')
        self.assertEqual(set(bundle['views']), {'2026-10', '2025-10'})
        self.assertEqual(bundle['views']['2026-10']['stores'][0]['gross'], 110)
        self.assertIsNone(bundle['views']['2026-10']['stores'][0]['profit'])
        self.assertEqual(bundle['views']['2025-10']['stores'][0]['gross'], 100)
        self.assertNotIn('private diagnostic', json.dumps(bundle))


if __name__ == '__main__':
    unittest.main()
