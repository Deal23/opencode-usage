import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opencode_usage.update import cleanup_stage


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
