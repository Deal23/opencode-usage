import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opencode_usage.update import cleanup_stage, clean_workspace


class UpdateCleanupTests(unittest.TestCase):
    def test_only_recognized_files_in_installer_stage_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp, patch('tempfile.gettempdir', return_value=tmp):
            stage = Path(tmp) / 'ocu-install-test'
            stage.mkdir()
            for name in ('install.py', 'result.txt', 'update.log', 'opencode_usage-1.whl', 'user.txt'):
                (stage / name).write_text('data')
            cleanup_stage(stage)
            self.assertEqual([p.name for p in stage.iterdir()], ['user.txt'])

    def test_unrecognized_location_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp, patch('tempfile.gettempdir', return_value=tmp):
            stage = Path(tmp) / 'workspace'
            stage.mkdir()
            (stage / 'result.txt').write_text('data')
            cleanup_stage(stage)
            self.assertTrue((stage / 'result.txt').exists())

    def test_workspace_overwrite_preserves_data_and_removes_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for name in ('ledger.db', 'workspace-owner.json', 'report-settings.json', 'model-prices.json', '.ocusage.lock', 'unknown.txt'):
                (home / name).write_text('data')
            for name in ('prices', 'backups', 'reports', 'manual-exports', '.state'):
                (home / name).mkdir()
            (home / '.state/deepseek-price-migration.json').write_text('{}')
            (home / '.state/sync.log').write_text('log')
            clean_workspace(home, 'overwrite')
            for name in ('ledger.db', 'workspace-owner.json', 'report-settings.json', 'model-prices.json', '.ocusage.lock', 'prices', 'backups', '.state/deepseek-price-migration.json'):
                self.assertTrue((home / name).exists(), name)
            for name in ('unknown.txt', 'reports', 'manual-exports', '.state/sync.log'):
                self.assertFalse((home / name).exists(), name)

    def test_archive_preserves_nested_price_and_moves_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / 'custom').mkdir()
            (home / 'custom/price.json').write_text('[]')
            (home / 'menu-config.json').write_text(json.dumps({'active_price_file': str(home / 'custom/price.json')}))
            (home / 'unknown.txt').write_text('user content')
            (home / '.state').mkdir()
            (home / '.state/sync.log').write_text('log')
            archive = clean_workspace(home, 'archive')
            self.assertEqual((archive / 'unknown.txt').read_text(), 'user content')
            self.assertEqual((archive / '.state/sync.log').read_text(), 'log')
            self.assertTrue((home / 'custom/price.json').exists())
