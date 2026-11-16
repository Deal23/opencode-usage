import unittest
from unittest.mock import patch

from opencode_usage import menu, sessions


class SessionTests(unittest.TestCase):
    def test_descendants_include_nested_children(self):
        rows = [{'id': 'ses-a'}, {'id': 'ses-b', 'parentID': 'ses-a'},
                {'id': 'ses-c', 'parentID': 'ses-b'}, {'id': 'ses-d'}]
        self.assertEqual(sessions.descendants(rows, {'ses-a'}), {'ses-a', 'ses-b', 'ses-c'})

    def test_number_ranges(self):
        self.assertEqual(sessions.parse_selection('2-3,5,7-9', 9), [1, 2, 4, 6, 7, 8])
        self.assertEqual(sessions.parse_selection('1，1-2', 3), [0, 1])
        for value in ('3-2', '0', '1-10', '1,,2', '-1', '1-'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sessions.parse_selection(value, 9)

    def test_remote_service_is_rejected(self):
        with patch.object(sessions, 'api', return_value={'urls': ['https://example.com']}):
            with self.assertRaises(ValueError):
                sessions.local_service()

    def test_pagination_and_archived_sessions(self):
        pages = [None, {'data': [{'id': 'ses-a'}], 'cursor': {'next': 'next'}},
                 {'data': [{'id': 'ses-b'}], 'cursor': {'next': None}},
                 {'data': [{'id': 'ses-c'}], 'cursor': {'next': None}}]
        pages[0] = {'urls': ['http://127.0.0.1:1234']}
        with patch.object(sessions, 'api', side_effect=pages):
            self.assertEqual(len(sessions.list_sessions()), 3)

    def test_delete_requires_confirmation_and_includes_children(self):
        rows = [{'id': 'ses-a', 'title': 'A'}, {'id': 'ses-b', 'parentID': 'ses-a'}]
        with patch.object(sessions, 'list_sessions', side_effect=[rows, rows, rows]), \
             patch.object(sessions, 'active_ids', return_value=set()), \
             patch.object(sessions, 'api') as api, \
             patch.object(menu, 'prompt', side_effect=['d', '1', 'DELETE', '0']), \
             patch.object(menu, 'notice'):
            menu.session_menu()
        api.assert_called_once_with('delete', '/api/session/ses-a')

    def test_active_child_prevents_delete(self):
        rows = [{'id': 'ses-a'}, {'id': 'ses-b', 'parentID': 'ses-a'}]
        with patch.object(sessions, 'list_sessions', return_value=rows), \
             patch.object(sessions, 'active_ids', return_value={'ses-b'}), \
             patch.object(sessions, 'api') as api, \
             patch.object(menu, 'prompt', side_effect=['d', '1', '0']), \
             patch.object(menu, 'notice'):
            menu.session_menu()
        api.assert_not_called()

    def test_rename_uses_api(self):
        rows = [{'id': 'ses-a', 'title': 'Old'}]
        with patch.object(sessions, 'list_sessions', return_value=rows), \
             patch.object(sessions, 'local_service'), patch.object(sessions, 'api') as api, \
             patch.object(menu, 'prompt', side_effect=['r', '1', 'New', '0']), \
             patch.object(menu, 'notice'):
            menu.session_menu()
        api.assert_called_once_with('patch', '/api/session/ses-a', {'title': 'New'})

    def test_open_session_uses_current_location(self):
        from unittest.mock import Mock
        with patch.object(sessions, 'local_service'), \
             patch.object(sessions, 'api', return_value={'data': {'id': 'ses-a', 'location': {'directory': '/demo'}}}), \
             patch.object(sessions.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            sessions.open_session({'id': 'ses-a'})
        run.assert_called_once_with(['opencode', '--session', 'ses-a', '/demo'], check=False)

    def test_open_session_rejects_missing_session(self):
        with patch.object(sessions, 'local_service'), patch.object(sessions, 'api', return_value={'data': {}}), \
             patch.object(sessions.subprocess, 'run') as run, self.assertRaises(ValueError):
            sessions.open_session({'id': 'ses-a'})
        run.assert_not_called()

    @unittest.skipIf(__import__('os').name == 'nt', 'POSIX terminal settings')
    def test_terminal_state_restores_after_error(self):
        import os
        import pty
        import termios
        from unittest.mock import Mock
        master, slave = pty.openpty()
        try:
            original = termios.tcgetattr(slave)
            fake_stdin = Mock()
            fake_stdin.fileno.return_value = slave
            fake_stdout = Mock()
            fake_stdout.isatty.return_value = False
            with patch.object(sessions.sys, 'stdin', fake_stdin), \
                 patch.object(sessions.sys, 'stdout', fake_stdout), self.assertRaises(RuntimeError):
                with sessions.terminal_state():
                    changed = termios.tcgetattr(slave)
                    changed[3] &= ~(termios.ECHO | termios.ICANON)
                    termios.tcsetattr(slave, termios.TCSANOW, changed)
                    raise RuntimeError('child failure')
            restored = termios.tcgetattr(slave)
            self.assertEqual(restored[3] & (termios.ECHO | termios.ICANON),
                             original[3] & (termios.ECHO | termios.ICANON))
        finally:
            os.close(master)
            os.close(slave)

    def test_changed_descendants_cancel_deletion(self):
        before = [{'id': 'ses-a'}]
        after = before + [{'id': 'ses-b', 'parentID': 'ses-a'}]
        with patch.object(sessions, 'list_sessions', side_effect=[before, after, after]), \
             patch.object(sessions, 'active_ids', return_value=set()), \
             patch.object(sessions, 'api') as api, \
             patch.object(menu, 'prompt', side_effect=['d', '1', 'DELETE', '0']), \
             patch.object(menu, 'notice'):
            menu.session_menu()
        api.assert_not_called()

    def test_table_shows_session_details(self):
        item = {'title': '示例会话', 'time': {'updated': 1700000000000},
                'tokens': {'input': 10, 'output': 20, 'reasoning': 3, 'cache': {'read': 40, 'write': 5}},
                'location': {'directory': '/demo/project'}}
        output = '\n'.join(sessions.table([item]))
        for value in ('会话名称', '最新使用时间', '累计 token', '工作目录', '示例会话', '0.00 M', '/demo/project'):
            self.assertIn(value, output)

    def test_tokens_right_aligned_with_two_decimals(self):
        rows = [{'tokens': {'input': n, 'cache': {}}} for n in (1234567, 12345678)]
        lines = sessions.table(rows)[2:]
        self.assertIn('1.23 M', lines[0])
        self.assertIn('12.35 M', lines[1])
        self.assertEqual(lines[0].index(' M'), lines[1].index(' M'))

    def test_table_preserves_long_directory(self):
        directory = '/demo/' + 'very-long-directory-' * 8
        self.assertIn(directory, '\n'.join(sessions.table([{'location': {'directory': directory}}])))

    def test_checkbox_selection_and_confirmation(self):
        rows = [{'id': 'ses-a'}, {'id': 'ses-b'}]
        with patch.object(sessions, 'list_sessions', side_effect=[rows, rows, rows]), \
             patch.object(sessions, 'choose_checkboxes', return_value=[0, 1]), \
             patch.object(sessions, 'active_ids', return_value=set()), \
             patch.object(sessions, 'api') as api, \
             patch.object(menu, 'prompt', side_effect=['b', 'DELETE', '0']), \
             patch.object(menu, 'notice'):
            menu.session_menu()
        self.assertEqual(api.call_count, 2)


if __name__ == '__main__':
    unittest.main()
