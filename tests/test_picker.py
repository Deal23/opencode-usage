import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from opencode_usage import actions


class PickerTests(unittest.TestCase):
    def test_file_filters_and_multiple_selection(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform):
                root = Mock()
                dialog = types.ModuleType('tkinter.filedialog')
                dialog.askopenfilenames = Mock(return_value=('/demo/a.snapshot.json', '/demo/b.json'))
                tk = types.ModuleType('tkinter')
                tk.Tk = Mock(return_value=root)
                tk.filedialog = dialog
                with patch.dict(sys.modules, {'tkinter': tk, 'tkinter.filedialog': dialog}), \
                     patch.object(actions.sys, 'platform', platform):
                    result = actions.choose_paths()
                self.assertEqual(result, [Path('/demo/a.snapshot.json'), Path('/demo/b.json')])
                filters = dialog.askopenfilenames.call_args.kwargs['filetypes']
                self.assertEqual(filters, [('JSON', '*.json')] if platform == 'darwin' else
                                 [('OCU 快照', '*.snapshot.json'), ('JSON', '*.json')])
                root.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
