import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from collector.capture import archive


class ArchiveTest(unittest.TestCase):
    def test_identical_download_keeps_one_original_and_two_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.xls'
            source.write_bytes(b'original report bytes')
            first = archive(source, root, {'store': 'A'})
            second = archive(source, root, {'store': 'B'})
            self.assertEqual(first, second)
            self.assertEqual(len(list((root / 'private-originals').iterdir())), 1)
            self.assertEqual((root / 'private-originals' / (first + '.xls')).read_bytes(), source.read_bytes())
            with closing(sqlite3.connect(root / 'captures.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM captures').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
