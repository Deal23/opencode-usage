import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from opencode_usage import actions, core


class SnapshotExportTests(unittest.TestCase):
    def test_paths_privacy_and_existing_file(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            ledger = home/'ledger.db'
            with closing(core.connect(ledger)):
                pass
            with patch.object(core, 'DEFAULT_LEDGER', ledger):
                default = actions.export_snapshot(home, 'device', 'local')
                self.assertEqual(default.parent, (home/'manual-exports').resolve())
                self.assertEqual(core.read_snapshot(default)['device'], 'device')
                custom = home/'custom.snapshot.json'
                actions.export_snapshot(home, 'device', 'local', str(custom), False)
                self.assertEqual(core.read_snapshot(custom)['sessions'], [])
                contents = custom.read_bytes()
                with self.assertRaises(ValueError):
                    actions.export_snapshot(home, 'device', 'local', str(custom))
                self.assertEqual(custom.read_bytes(), contents)
                directory = home/'chosen'
                directory.mkdir()
                self.assertEqual(actions.export_snapshot(home, 'device', 'local', str(directory)).parent, directory.resolve())


if __name__ == '__main__':
    unittest.main()
