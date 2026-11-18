import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from tempfile import TemporaryDirectory
from pathlib import Path

from opencode_usage import report_server


class ReportServerTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            {'id': 'ses_parent', 'title': 'parent', 'location': {'directory': '/test'}},
            {'id': 'ses_child', 'title': 'child', 'parentID': 'ses_parent'},
        ]

    @patch('opencode_usage.report_server.sys.platform', 'darwin')
    @patch('opencode_usage.report_server.subprocess.run')
    def test_snapshot_picker_handles_selection_and_cancel(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = '/Users/example/a snapshot.json\n'
        self.assertEqual(report_server.choose_snapshot(), '/Users/example/a snapshot.json')
        run.return_value.returncode = 1
        run.return_value.stderr = 'User canceled. (-128)'
        self.assertIsNone(report_server.choose_snapshot())

    @patch('opencode_usage.report_server.sessions.active_ids', return_value=set())
    @patch('opencode_usage.report_server.sessions.list_sessions')
    @patch('opencode_usage.report_server.sessions.api')
    def test_delete_requires_current_descendants(self, api, listing, active):
        listing.return_value = self.items
        preview = report_server.perform('preview-delete', {'id': 'ses_parent'})
        self.assertEqual({s['id'] for s in preview}, {'ses_parent', 'ses_child'})
        with self.assertRaises(ValueError):
            report_server.perform('delete', {'id': 'ses_parent', 'affected': ['ses_parent']})
        api.assert_not_called()
        report_server.perform('delete', {'id': 'ses_parent', 'affected': ['ses_parent', 'ses_child']})
        api.assert_called_once_with('delete', '/api/session/ses_parent')

    @patch('opencode_usage.report_server.sessions.active_ids', return_value={'ses_child'})
    @patch('opencode_usage.report_server.sessions.list_sessions')
    def test_delete_blocks_active_child(self, listing, active):
        listing.return_value = self.items
        with self.assertRaises(ValueError):
            report_server.perform('preview-delete', {'id': 'ses_parent'})

    @patch('opencode_usage.report_server.sessions.api')
    @patch('opencode_usage.report_server.sessions.active_ids', return_value=set())
    @patch('opencode_usage.report_server.sessions.list_sessions')
    def test_batch_delete_deduplicates_parent_and_child(self, listing, active, api):
        listing.return_value = self.items
        ids = ['ses_parent', 'ses_child']
        preview = report_server.perform('preview-batch-delete', {'ids': ids})
        with self.assertRaises(ValueError):
            report_server.perform('batch-delete', {'ids': ids, 'affected': ['ses_parent']})
        api.assert_not_called()
        report_server.perform('batch-delete', {'ids': ids, 'affected': [item['id'] for item in preview]})
        api.assert_called_once_with('delete', '/api/session/ses_parent')

    @patch('opencode_usage.report_server.sessions.active_ids', return_value={'ses_child'})
    @patch('opencode_usage.report_server.sessions.list_sessions')
    def test_batch_delete_rejects_active_descendant(self, listing, active):
        listing.return_value = self.items
        with self.assertRaises(ValueError):
            report_server.perform('preview-batch-delete', {'ids': ['ses_parent']})

    @patch('opencode_usage.report_server.sessions.api')
    @patch('opencode_usage.report_server.sessions.list_sessions')
    def test_rename_validates_and_updates_current_session(self, listing, api):
        listing.return_value = self.items
        report_server.perform('rename', {'id': 'ses_parent', 'title': ' New title '})
        api.assert_called_once_with('patch', '/api/session/ses_parent', {'title': 'New title'})
        with self.assertRaises(ValueError):
            report_server.perform('rename', {'id': 'ses_missing', 'title': 'x'})

    @patch('opencode_usage.report_server.subprocess.run')
    @patch('opencode_usage.report_server.sessions.session_command')
    @patch('opencode_usage.report_server.sys.platform', 'darwin')
    def test_macos_launch_quotes_shell_arguments(self, command, run):
        command.return_value = ['opencode', '--session', 'ses_1', '/path/with space']
        report_server.launch_session(self.items[0])
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ['osascript', '-e'])
        self.assertIn("'/path/with space'", args[2])

    def test_server_requires_origin_and_token(self):
        with TemporaryDirectory() as folder:
            page = Path(folder) / 'report.html'
            page.write_text('report', encoding='utf-8')
            url = report_server.start(page)
            origin, token = url.split('/#')
            self.assertEqual(urlopen(origin + '/').read(), b'report')
            def request(headers):
                return urlopen(Request(origin + '/list', data=b'{}', headers=headers))
            with self.assertRaises(HTTPError) as error:
                request({'Origin': origin, 'X-OCU-Token': 'wrong'})
            self.assertEqual(error.exception.code, 403)
            with self.assertRaises(HTTPError) as error:
                request({'Origin': 'https://example.org', 'X-OCU-Token': token})
            self.assertEqual(error.exception.code, 403)
            with patch('opencode_usage.report_server.sessions.list_sessions', return_value=self.items):
                result = json.load(request({'Origin': origin, 'X-OCU-Token': token}))
            self.assertEqual(len(result['data']), 2)

    def test_report_refresh_requires_token_and_invokes_callback(self):
        with TemporaryDirectory() as folder:
            page = Path(folder) / 'report.html'
            page.write_text('report', encoding='utf-8')
            calls = []
            def refresh():
                calls.append(True)
                return {'ok': True}
            origin, token = report_server.start(page, refresh=refresh).split('/#')
            request = Request(origin + '/refresh-report', data=b'{}', headers={'Origin': origin, 'X-OCU-Token': token})
            self.assertTrue(json.load(urlopen(request))['data']['ok'])
            self.assertEqual(calls, [True])


if __name__ == '__main__':
    unittest.main()
