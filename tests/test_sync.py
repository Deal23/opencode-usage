import io
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from opencode_usage import core, menu
from opencode_usage.workspace import state_path, organize_state


class SyncTests(unittest.TestCase):
    def test_export_menu_accepts_default_or_custom_path_directly(self):
        config = {'device_name': 'local'}
        for destination in ('', 'custom'):
            custom = self.home/'external/saved.json'
            answers = ['2', str(custom) if destination else '', '1', '0']
            with patch('opencode_usage.menu.prompt', side_effect=answers), patch.object(core, 'export') as export, patch('opencode_usage.actions.prune_exports'), patch('opencode_usage.actions.export_summary', return_value='exported'), patch('sys.stdout', io.StringIO()) as output:
                menu.data_menu(self.home, config, 'local')
            dest = export.call_args.args[2]
            self.assertEqual(dest, custom) if destination else self.assertEqual(dest.parent, self.home/'exports')
            self.assertNotIn('选择导出位置', output.getvalue())
            self.assertNotIn('x 自定义', output.getvalue())
            self.assertIn('[3] 同步设置', output.getvalue())
            self.assertNotIn('[6]', output.getvalue())

    def test_export_destination_cancel_never_exports(self):
        with patch('opencode_usage.menu.prompt', side_effect=['2', '', '0', '0']), patch.object(core, 'export') as export, patch('sys.stdout', io.StringIO()):
            menu.data_menu(self.home, {'device_name': 'local'}, 'local')
        export.assert_not_called()

    def test_help_describes_all_menu_actions(self):
        from opencode_usage.help import TOPICS, DATA, SETTINGS, ADVANCED, explain
        self.assertEqual([title for title, _ in TOPICS.values()], ['刷新与报告', '数据', '设置', '参考说明'])
        self.assertEqual([title for title, _ in DATA.values()][:5], ['导入', '导出', '同步设置', '导出 CSV', '打开目录'])
        self.assertEqual([title for title, _ in SETTINGS.values()], ['设备名称', '偏好', '报表', '更新', '高级'])
        self.assertIs(SETTINGS['5'][1], ADVANCED)
        with patch('sys.stdout', io.StringIO()) as output:
            explain('帮助', TOPICS)
        text = output.getvalue()
        for label in ('默认完整文件路径', '自定义', 'RESTORE', '帮助 → 数据 → 导出', '帮助 → 设置 → 高级 → 诊断'):
            self.assertIn(label, text)

    def test_help_expands_only_selected_action(self):
        from opencode_usage.help import show
        with patch('opencode_usage.help.prompt', side_effect=['2', '2', '', '0', '0']), patch('sys.stdout', io.StringIO()) as output:
            show(self.home)
        text = output.getvalue()
        self.assertIn('帮助 → 数据 → 导出', text)
        self.assertIn('年月日时分秒', text)
        self.assertNotIn('恢复需输入 RESTORE', text)

    def test_report_preferences_validate_before_saving_and_preserve_prices(self):
        from opencode_usage.reporting import settings
        core.atomic_json(self.home/'report-settings.json', {'timezone_offset': 8, 'usd_to_cny': 7, 'prices': []})
        with patch('opencode_usage.menu.prompt', side_effect=['1', '99', '1', '5.5', '2', 'nan', '2', '7.2', '0']), patch('sys.stdout', io.StringIO()):
            menu.report_preferences(self.home)
        saved = settings(self.home)
        self.assertEqual(saved['timezone_offset'], 5.5)
        self.assertEqual(saved['usd_to_cny'], 7.2)
        self.assertEqual(saved['prices'], [])

    def test_settings_read_only_navigation_does_not_write_config(self):
        config = {'device_name': 'local'}
        with patch('opencode_usage.menu.prompt', side_effect=['2', '0', '3', '0', '5', '0', '0']), patch.object(core, 'atomic_json') as write, patch('sys.stdout', io.StringIO()):
            menu.settings_menu(self.home, config, 'local')
        write.assert_not_called()

    def test_sync_preferences_require_exchange_and_privacy_confirmation(self):
        config = {'device_name': 'local'}
        with patch('opencode_usage.menu.prompt', side_effect=['2', '3', '0']), patch.object(core, 'atomic_json') as write, patch('sys.stdout', io.StringIO()):
            menu.sync_preferences(self.home, config)
        write.assert_not_called()
        config['exchange'] = str(self.home/'exchange')
        with patch('opencode_usage.menu.prompt', side_effect=['2', 'n', '2', 'y', '3', '0']), patch('sys.stdout', io.StringIO()):
            menu.sync_preferences(self.home, config)
        self.assertTrue(config['sync_metadata'])
        self.assertTrue(config['relay_history'])

    def test_backup_retention_setting_is_with_backups(self):
        config = {'device_name': 'local'}
        with patch('opencode_usage.menu.prompt', side_effect=['k', '8']), patch('sys.stdout', io.StringIO()):
            menu.settings_action(self.home, config, 'local', '7')
        self.assertEqual(config['backup_keep'], 8)

    def test_empty_inbox_cleanup_permission_error_is_nonfatal(self):
        from opencode_usage.workspace import organize_inbox
        (self.home/'imports').mkdir()
        with patch.object(Path, 'rmdir', side_effect=PermissionError('busy')), patch('sys.stdout', io.StringIO()) as output:
            organize_inbox(self.home)
        self.assertTrue((self.home/'imports').exists())
        self.assertIn('不影响使用', output.getvalue())

    def test_cli_report_csv_is_explicit(self):
        import subprocess
        import sys
        core.store([self.row], self.ledger)
        snapshot = self.home/'source.json'
        core.export(self.ledger, 'local', snapshot)
        dest = self.home/'output/report.html'
        command = [sys.executable, '-m', 'opencode_usage', 'report', str(snapshot), '--output', str(dest)]
        subprocess.run(command, capture_output=True, check=True)
        self.assertFalse((dest.parent/'total.csv').exists())
        subprocess.run(command+['--csv'], capture_output=True, check=True)
        self.assertTrue((dest.parent/'total.csv').exists())
        self.assertTrue((dest.parent/'reference-costs.csv').exists())

    def test_legacy_inbox_migration_preserves_unknown_and_conflicts(self):
        from opencode_usage.workspace import organize_inbox, inbox
        legacy = self.home/'imports'
        legacy.mkdir()
        source = legacy/'pending.snapshot.json'
        source.write_bytes(b'pending')
        unknown = legacy/'personal.txt'
        unknown.write_bytes(b'keep')
        organize_inbox(self.home)
        self.assertEqual((inbox(self.home)/source.name).read_bytes(), b'pending')
        self.assertTrue(unknown.exists())
        source.write_bytes(b'conflict')
        with self.assertRaises(ValueError):
            organize_inbox(self.home)
        self.assertEqual(source.read_bytes(), b'conflict')
        self.assertEqual((inbox(self.home)/source.name).read_bytes(), b'pending')

    def test_empty_legacy_inbox_removed_without_creating_state(self):
        from opencode_usage.workspace import organize_inbox
        (self.home/'imports').mkdir()
        organize_inbox(self.home)
        self.assertFalse((self.home/'imports').exists())
        self.assertFalse((self.home/'.state').exists())

    def test_data_menu_import_uses_single_path_prompt_and_no_report_on_cancel(self):
        source = self.home/'external.snapshot.json'
        with patch('opencode_usage.menu.prompt', side_effect=['1', str(source), '0']) as ask, patch('opencode_usage.menu.merge_files', return_value=dict(added=0, updated=0, metadata_updated=0)), patch('opencode_usage.menu.show_report') as report, patch('sys.stdout', io.StringIO()):
            menu.data_menu(self.home, {}, 'local')
        self.assertEqual(ask.call_count, 3)
        report.assert_not_called()

    def test_view_report_does_not_refresh_or_sync(self):
        core.atomic_json(self.home/'menu-config.json', {'device_name': 'local', 'check_updates': False})
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.menu.identity', return_value='local'), patch('opencode_usage.menu.ensure_linux_path'), patch('opencode_usage.menu.prompt', side_effect=['v', '0']), patch('opencode_usage.menu.refresh') as refresh, patch('opencode_usage.actions.view_report') as view, patch('sys.stdout', io.StringIO()):
            menu._main()
        view.assert_called_once_with(self.home)
        refresh.assert_not_called()

    def test_csv_only_export_does_not_replace_reports_or_refresh_implicitly(self):
        from opencode_usage import actions
        core.store([self.row], self.ledger)
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('sys.stdout', io.StringIO()):
            actions.show_report('local', [], open_report=False)
            page = self.home/'reports/report.html'
            before = page.read_bytes()
            actions.export_csv(self.home)
            csv = self.home/'reports/total.csv'
            self.assertTrue(csv.exists())
            self.assertEqual(page.read_bytes(), before)
            csv.write_bytes(b'user CSV')
            actions.show_report('local', [], open_report=False)
            self.assertEqual(csv.read_bytes(), b'user CSV')

    def test_export_filename_keeps_device_and_portable_timestamp(self):
        from opencode_usage.actions import export_filename
        self.assertRegex(export_filename('我的电脑: Y7000/Win'), r'^usage-我的电脑_ Y7000_Win-\d{8}-\d{6}\.snapshot\.json$')

    def test_export_retention_requires_success_and_preserves_unknown_files(self):
        from opencode_usage.actions import prune_exports
        core.store([self.row], self.ledger)
        folder = self.home/'exports'
        old = folder/'usage-20261113T044309971618Z.snapshot.json'
        latest = folder/'usage-我的电脑-20261113-163025.snapshot.json'
        core.export(self.ledger, 'local', old)
        with self.assertRaises(OSError):
            prune_exports(self.home, latest)
        self.assertTrue(old.exists())
        core.export(self.ledger, 'local', latest)
        unknown = folder/'personal.json'
        unknown.write_text('keep', encoding='utf-8')
        prune_exports(self.home, latest)
        self.assertFalse(old.exists())
        self.assertTrue(latest.exists())
        self.assertTrue(unknown.exists())

    def test_safe_cleanup_only_removes_obsolete_report_after_success(self):
        from opencode_usage import actions
        core.store([self.row], self.ledger)
        reports = self.home/'reports'
        obsolete = reports/'total.snapshot.json'
        core.export(self.ledger, 'local', obsolete)
        personal = reports/'personal.json'
        personal.write_text('keep', encoding='utf-8')
        before = obsolete.read_bytes()
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('sys.stdout', io.StringIO()):
            with patch('opencode_usage.dashboard.render', side_effect=OSError('disk full')):
                self.assertIsNone(actions.show_report('local', open_report=False))
            self.assertEqual(obsolete.read_bytes(), before)
            self.assertIsNotNone(actions.show_report('local', [], open_report=False))
            self.assertFalse(obsolete.exists())
            self.assertEqual(personal.read_text(encoding='utf-8'), 'keep')
            obsolete.write_text('unknown', encoding='utf-8')
            actions.show_report('local', [], open_report=False)
            self.assertEqual(obsolete.read_text(encoding='utf-8'), 'unknown')

    def test_state_migration_preserves_bytes_and_user_files(self):
        files = {'sync-state.json': b'{"devices":{}}', 'sync.log': b'old log\r\n',
                 'last-sync.json': b'{"ok":true}'}
        for name, data in files.items():
            (self.home/name).write_bytes(data)
        personal = self.home/'menu-config.before-test.json'
        personal.write_bytes(b'personal')
        core.store([self.row], self.ledger)
        ledger = self.ledger.read_bytes()
        self.assertEqual(state_path(self.home, 'sync.log'), self.home/'sync.log')
        organize_state(self.home)
        organize_state(self.home)
        for name, data in files.items():
            self.assertFalse((self.home/name).exists())
            self.assertEqual(state_path(self.home, name).read_bytes(), data)
        self.assertEqual(personal.read_bytes(), b'personal')
        self.assertEqual(self.ledger.read_bytes(), ledger)

    def test_state_migration_preflights_conflicts_without_overwriting(self):
        (self.home/'last-sync.json').write_bytes(b'legacy')
        (self.home/'sync.log').write_bytes(b'log')
        folder = self.home/'.state'
        folder.mkdir()
        (folder/'last-sync.json').write_bytes(b'new')
        with self.assertRaisesRegex(ValueError, '冲突'):
            organize_state(self.home)
        self.assertEqual((self.home/'last-sync.json').read_bytes(), b'legacy')
        self.assertEqual((folder/'last-sync.json').read_bytes(), b'new')
        self.assertTrue((self.home/'sync.log').exists())
        with self.assertRaisesRegex(ValueError, '冲突'):
            state_path(self.home, 'last-sync.json')

    def test_empty_startup_does_not_create_unused_directories(self):
        core.atomic_json(self.home/'menu-config.json', {'device_name': 'local', 'check_updates': False})
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.menu.identity', return_value='local'), patch('opencode_usage.menu.ensure_linux_path'), patch('opencode_usage.menu.prompt', return_value='0'), patch('sys.stdout', io.StringIO()):
            menu.main()
        for folder in ('imports', 'exports', 'reports', '.state'):
            self.assertFalse((self.home/folder).exists())

    def test_configuration_is_shared_and_rejects_non_objects(self):
        from opencode_usage import automation, interaction
        self.assertIs(automation.configuration, interaction.configuration)
        self.assertIs(menu.configuration, interaction.configuration)
        core.atomic_json(self.home/'menu-config.json', [])
        with self.assertRaisesRegex(ValueError, 'JSON 对象'):
            interaction.configuration(self.home)
        core.atomic_json(self.home/'menu-config.json', {'db': '~/source.db', 'exchange': '~/exchange'})
        config = interaction.configuration(self.home)
        self.assertEqual(config['db'], str(Path.home()/'source.db'))
        self.assertEqual(config['exchange'], str(Path.home()/'exchange'))

    def test_atomic_json_preserves_original_on_invalid_payload(self):
        path = self.home/'config.json'
        core.atomic_json(path, {'name': '中文'})
        before = path.read_bytes()
        for value in (float('nan'), object()):
            with self.assertRaises((ValueError, TypeError)):
                core.atomic_json(path, {'value': value})
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.home.glob('.config.json-*')), [])
        self.assertEqual(json.loads(before), {'name': '中文'})

    def test_shared_comparison_agrees_with_preview_and_merge(self):
        from opencode_usage.actions import preview
        from opencode_usage.sync import merge_snapshot
        core.store([self.row], self.ledger)
        snapshot = dict(version=2, device='remote', records=[self.row])
        old = tuple(self.row[k] for k in core.FIELDS[1:])
        for key, row in (('same', self.row), ('updated', dict(self.row, output=self.row['output']+1)),
                         ('conflicts', dict(self.row, model='different'))):
            self.assertEqual(core.compare(row, old), key)
            data = dict(snapshot, records=[row])
            self.assertEqual(preview(data, self.ledger)[key], 1)
            if key == 'conflicts':
                with self.assertRaises(ValueError):
                    merge_snapshot(data, self.ledger)
            else:
                self.assertEqual(merge_snapshot(data, self.ledger)[key], 1)

    def test_unchanged_refresh_omits_zero_counts_and_repeated_paths(self):
        from opencode_usage.automation import refresh
        core.store([self.row], self.ledger)
        config = {'device_name': 'local'}
        core.atomic_json(self.home/'menu-config.json', config)
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.automation.discover', return_value=None), patch('sys.stdout', io.StringIO()) as output:
            result = refresh(config, 'local', self.home, open_report=False)
        self.assertFalse(result['ok'])
        text = output.getvalue()
        self.assertIn('本机无新增', text)
        self.assertNotIn('更新 0', text)
        self.assertNotIn('失败 0', text)
        self.assertNotIn('HTML  ', text)
        self.assertFalse((self.home/'reports/total.csv').exists())
        self.assertFalse((self.home/'reports/reference-costs.csv').exists())

    def test_settings_are_one_option_per_line(self):
        with patch('opencode_usage.menu.prompt', return_value='0'), patch('sys.stdout', io.StringIO()) as output:
            menu.settings_menu(self.home, {'device_name': 'local'}, 'local')
        lines = output.getvalue().splitlines()
        self.assertEqual(sum('[1]' in line for line in lines), 1)
        self.assertTrue(any('[5] 高级' in line for line in lines))
        self.assertFalse(any('[9]' in line for line in lines))
        self.assertFalse(any('[1]' in line and '[2]' in line for line in lines))

    def test_preview_uses_report_timezone(self):
        from opencode_usage.actions import preview
        core.atomic_json(self.home/'report-settings.json', {'timezone_offset': -8})
        info = preview(dict(version=2, device='remote', records=[self.row]), self.ledger)
        self.assertEqual(info['start'], '1969-12-31')

    def test_shared_refresh_requests_input_only_through_callback(self):
        from opencode_usage.automation import refresh
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.automation.discover', return_value=None), patch('builtins.input', side_effect=AssertionError('must not prompt')), patch('sys.stdout', io.StringIO()):
            requested = []
            def request(kind, value):
                requested.append(kind)
                return self.home/'missing.db'
            result = refresh({'device_name': 'local'}, 'local', self.home, request_input=request, open_report=False)
        self.assertEqual(requested, ['source'])
        self.assertFalse(result['ok'])
        self.assertFalse(self.ledger.exists())

    def test_panel_summary_only_shows_actionable_warnings(self):
        from opencode_usage.actions import summary
        core.atomic_json(self.home/'report-status.json', dict(at='11/13 15:11', records=5984, tokens=610700000, devices=3, failures=0))
        text = summary(self.home)
        self.assertEqual(text, '报告 11/13 15:11 · 5,984 条 · 610.7M token')
        core.atomic_json(self.home/'report-status.json', dict(at='11/13 15:11', records=5984, tokens=610700000, failures=2))
        self.assertIn('\n注意：2 份导入失败', summary(self.home))

    def test_preferences_modify_only_selected_item(self):
        config = {'device_name': 'local'}
        with patch('opencode_usage.menu.prompt', side_effect=['2', '0']), patch('sys.stdout', io.StringIO()):
            menu.preferences(self.home, config)
        self.assertFalse(config['open_browser'])
        self.assertNotIn('backup_keep', config)
        self.assertNotIn('sync_metadata', config)
        self.assertNotIn('relay_history', config)
        self.assertEqual(json.loads((self.home/'menu-config.json').read_text(encoding='utf-8')), config)

    def test_settings_path_error_allows_retry_without_exiting(self):
        config = {'device_name': 'local'}
        valid = self.home/'source.db'
        valid.touch()
        with patch('opencode_usage.menu.prompt', side_effect=['5', '1', str(self.home/'missing.db'), '1', str(valid), '0', '0']), patch('sys.stdout', io.StringIO()) as output:
            self.assertFalse(menu.settings_menu(self.home, config, 'local'))
        self.assertEqual(config['db'], str(valid.resolve()))
        self.assertIn('可修正后重试', output.getvalue())

    def test_diagnostics_shows_summary_instead_of_raw_json(self):
        checks = {'checks': [{'name': 'source', 'ok': False}, {'name': 'ledger', 'ok': True}]}
        with patch('opencode_usage.automation.diagnose', return_value=checks), patch('opencode_usage.menu.prompt', return_value='n'), patch('sys.stdout', io.StringIO()) as output:
            menu.diagnostics(self.home, {})
        self.assertIn('本机数据库：异常', output.getvalue())
        self.assertIn('历史台账：正常', output.getvalue())
        self.assertNotIn('"checks"', output.getvalue())

    def test_cli_report_reads_once_and_merges_metadata_by_version_in_either_order(self):
        from opencode_usage.dashboard import metadata_schema
        core.store([self.row], self.ledger)
        with closing(core.connect(self.ledger)) as db, db:
            metadata_schema(db)
            db.execute("INSERT INTO session_meta(session_id,title,project,time_updated) VALUES ('s','new','/new',200)")
        new, old = self.home/'new.json', self.home/'old.json'
        core.export(self.ledger, 'remote', new, include_metadata=True)
        data = core.read_snapshot(new)
        data['sessions'][0].update(title='old', project='/old', time_updated=100)
        core.atomic_json(old, data)
        seen = []
        def render(ledger, dest, **kwargs):
            with closing(core.readonly(ledger)) as db:
                seen.append(db.execute('SELECT title,project,time_updated FROM session_meta').fetchone())
        for paths in ([new, old], [old, new]):
            with patch.object(core, 'read_snapshot', wraps=core.read_snapshot) as read, patch('opencode_usage.dashboard.render', side_effect=render):
                self.assertEqual(core.report(paths, self.home/'report.html'), (1, []))
                self.assertEqual(read.call_count, 2)
        self.assertEqual(seen, [('new', '/new', 200)]*2)

    def test_automatic_sync_reports_metadata_only_changes_and_retains_bad_input(self):
        from opencode_usage.dashboard import metadata_schema
        core.store([self.row], self.ledger)
        remote = self.home/'remote.db'
        core.store([self.row], remote)
        with closing(core.connect(remote)) as db, db:
            metadata_schema(db)
            db.execute("INSERT INTO session_meta(session_id,title,project,time_updated) VALUES ('s','Title','/project',200)")
        inbox = self.home/'imports'
        inbox.mkdir()
        good, bad = inbox/'good.snapshot.json', inbox/'bad.snapshot.json'
        core.export(remote, 'remote', good, include_metadata=True)
        bad.write_text('{}', encoding='utf-8')
        config = {'db': str(self.home/'missing.db'), 'device_name': 'local'}
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.menu.prompt', side_effect=AssertionError('must not prompt')), patch('sys.stdout', io.StringIO()) as output:
            result = menu.refresh(config, 'local', self.home, interactive=False, open_report=False)
        self.assertEqual(result['imported']['added'], 0)
        self.assertEqual(result['imported']['updated'], 0)
        self.assertEqual(result['imported']['metadata_updated'], 1)
        self.assertEqual(result['imported']['failed'], 1)
        self.assertIn('会话信息补齐/更新 1 个', output.getvalue())
        saved = json.loads(state_path(self.home, 'last-sync.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['imported'], result['imported'])
        self.assertFalse(good.exists())
        self.assertTrue(bad.exists())

    def test_settings_stay_open_and_refresh_needs_no_extra_prompt(self):
        core.atomic_json(self.home/'menu-config.json', {'device_name': 'local', 'check_updates': False})
        # Enter settings, inspect report settings, return, refresh, quit.
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.menu.identity', return_value='local'), patch('opencode_usage.menu.ensure_linux_path'), patch('opencode_usage.menu.prompt', side_effect=['3', '3', '0', '0', '1', '0']) as ask, patch('opencode_usage.menu.refresh') as refresh, patch('opencode_usage.menu.menu') as main_menu, patch('opencode_usage.actions.open_directory'), patch('sys.stdout', io.StringIO()):
            menu._main()
        self.assertEqual(ask.call_count, 6)
        self.assertEqual(main_menu.call_count, 3)
        refresh.assert_called_once()

    def test_batch_reads_each_path_once_and_counts_actual_metadata_changes(self):
        from opencode_usage.dashboard import metadata_schema
        remote = self.home/'remote.db'
        core.store([self.row], remote)
        with closing(core.connect(remote)) as db, db:
            metadata_schema(db)
            db.execute("INSERT INTO session_meta(session_id,title,project,time_updated) VALUES ('s','Title','/project',200)")
        source = self.home/'complete.snapshot.json'
        core.export(remote, 'remote', source, include_metadata=True)
        core.store([self.row], self.ledger)
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch.object(core, 'read_snapshot', wraps=core.read_snapshot) as read, patch('opencode_usage.menu.prompt', return_value='y') as ask, patch('sys.stdout', io.StringIO()):
            result = menu.merge_files([source, source.parent/'.'/source.name], self.home, {})
        self.assertEqual((result['added'], result['same'], result['metadata_updated']), (0, 1, 1))
        read.assert_called_once()
        ask.assert_called_once()
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.menu.prompt', return_value='y'), patch('sys.stdout', io.StringIO()):
            result = menu.merge_files([source], self.home, {})
        self.assertEqual(result['metadata_updated'], 0)

    def test_failed_receipt_preserves_retry_input_and_external_source(self):
        from opencode_usage.sync import accept
        source = self.home/'external.json'
        core.store([self.row], self.ledger)
        core.export(self.ledger, 'remote', source)
        data = core.read_snapshot(source)
        with patch('opencode_usage.sync.receive', side_effect=OSError('receipt failed')):
            with self.assertRaises(OSError):
                accept(self.home, source, data, self.ledger)
        self.assertTrue(source.exists())
        self.assertEqual(len(list((self.home/'.state/inbox').glob('*.snapshot.json'))), 1)
        result = accept(self.home, source, data, self.ledger)
        self.assertEqual(result['same'], 1)
        self.assertFalse(list((self.home/'.state/inbox').glob('*.snapshot.json')))

    def test_validation_precedes_any_ledger_write(self):
        source = self.home/'invalid.json'
        core.atomic_json(source, dict(format='ocusage-snapshot', version=2, device='remote',
            records=[self.row], sources={'m': [{'id': '', 'name': 'bad'}]}))
        with self.assertRaises(ValueError):
            menu.import_snapshot(source, self.ledger)
        self.assertFalse(self.ledger.exists())

    def test_manual_export_defaults_complete_and_usage_only_is_explicit(self):
        with patch('opencode_usage.menu.prompt', return_value=''), patch('sys.stdout', io.StringIO()):
            self.assertTrue(menu.export_mode())
        with patch('opencode_usage.menu.prompt', return_value='2'), patch('sys.stdout', io.StringIO()):
            self.assertFalse(menu.export_mode())
        with patch('opencode_usage.menu.prompt', return_value='0'), patch('sys.stdout', io.StringIO()):
            self.assertIsNone(menu.export_mode())

    def test_cli_complete_export_then_usage_only_preserves_metadata_on_merge(self):
        from opencode_usage.dashboard import metadata_schema
        from opencode_usage.actions import export_summary
        import subprocess
        import sys
        core.store([self.row], self.ledger, {'m': [{'id':'mac','name':'Mac'}]})
        with closing(core.connect(self.ledger)) as db, db:
            metadata_schema(db)
            db.execute("INSERT INTO session_meta(session_id,title,project,time_updated) VALUES ('s','Title','/project',200)")
        complete, private = self.home/'complete.json', self.home/'private.json'
        command = [sys.executable,'-m','opencode_usage','export','--ledger',str(self.ledger),'--device','mac']
        subprocess.run(command+['--output',str(complete)], check=True, capture_output=True)
        subprocess.run(command+['--output',str(private),'--usage-only'], check=True, capture_output=True)
        self.assertEqual(json.loads(complete.read_text(encoding='utf-8'))['sessions'][0]['title'], 'Title')
        self.assertEqual(json.loads(private.read_text(encoding='utf-8'))['sessions'], [])
        self.assertIn('标题 1/1', export_summary(complete))
        target = self.home/'target.db'
        menu.import_snapshot(private,target)
        self.assertEqual(menu.import_snapshot(complete,target), 0)
        menu.import_snapshot(private,target)
        with closing(core.readonly(target)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT title,project,time_updated FROM session_meta').fetchone(),('Title','/project',200))

    def test_usage_and_metadata_import_roll_back_together(self):
        from opencode_usage.dashboard import metadata_schema
        core.store([], self.ledger)
        with closing(core.connect(self.ledger)) as db, db:
            metadata_schema(db)
            db.execute("CREATE TRIGGER reject_meta BEFORE INSERT ON session_meta BEGIN SELECT RAISE(ABORT,'metadata rejected'); END")
        remote = self.home/'remote.db'
        core.store([self.row], remote)
        with closing(core.connect(remote)) as db, db:
            metadata_schema(db)
            db.execute("INSERT INTO session_meta(session_id,title) VALUES ('s','title')")
        path = self.home/'snapshot.json'
        core.export(remote, 'remote', path, include_metadata=True)
        with self.assertRaises(sqlite3.Error):
            menu.import_snapshot(path, self.ledger)
        with closing(core.readonly(self.ledger)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 0)

    def test_metadata_versions_and_privacy_scope(self):
        from opencode_usage.dashboard import metadata_schema
        core.store([self.row], self.ledger, {'m': [{'id': 'local', 'name': 'Local'}]})
        with closing(core.connect(self.ledger)) as db, db:
            metadata_schema(db)
            db.execute("INSERT INTO session_meta(session_id,title,project,time_updated) VALUES ('s','new','/new',200)")
            db.execute("INSERT INTO session_meta(session_id,title,project,time_updated) VALUES ('private','secret','/secret',300)")
        path = self.home/'snapshot.json'
        core.export(self.ledger, 'local', path, include_metadata=True, local_only=True)
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual([s['session_id'] for s in data['sessions']], ['s'])
        target = self.home/'target.db'
        menu.import_snapshot(path, target)
        data['sessions'][0].update(title='old', project='/old', time_updated=100)
        core.atomic_json(path, data)
        menu.import_snapshot(path, target)
        with closing(core.readonly(target)) as db:
            self.assertEqual(db.execute('SELECT title,project FROM session_meta').fetchone(), ('new','/new'))
        data['sessions'][0].update(title='newest', project='', time_updated=400)
        core.atomic_json(path, data)
        menu.import_snapshot(path, target)
        with closing(core.readonly(target)) as db:
            self.assertEqual(db.execute('SELECT title,project FROM session_meta').fetchone(), ('newest','/new'))
        core.export(self.ledger, 'local', path)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['sessions'], [])

    def test_heartbeat_keeps_unchanged_snapshot_and_updates_presence(self):
        from opencode_usage.sync import publish, receive, read_heartbeats, status, validate_exchange
        with tempfile.TemporaryDirectory() as folder:
            exchange = Path(folder)
            core.store([self.row], self.ledger, {'m': [{'id':'local','name':'Local'}]})
            config = {'exchange':str(exchange), 'device_name':'Local'}
            core.atomic_json(self.home/'collection-status.json', {'ok':True,'last_success':'2026-01-01T00:00:00+00:00'})
            publish(self.home, self.ledger, 'local', config)
            path = exchange/'local.snapshot.json'
            original = path.read_bytes()
            publish(self.home, self.ledger, 'local', config)
            self.assertEqual(original, path.read_bytes())
            receive(self.home, json.loads(path.read_text(encoding='utf-8')), path)
            read_heartbeats(self.home, exchange)
            entry = status(self.home)['devices']['local']
            self.assertFalse(entry['stale'])
            self.assertTrue(entry['collection_ok'])
        with self.assertRaises(ValueError):
            validate_exchange(self.home, self.home/'nested')

    def test_diagnostics_allowlist_and_update_result(self):
        from opencode_usage.automation import diagnostic_bundle
        from opencode_usage.update import previous_result
        config = {'db':str(self.home/'secret-user.db')}
        path = diagnostic_bundle(self.home, config)
        text = path.read_text(encoding='utf-8')
        self.assertNotIn(str(self.home), text)
        self.assertNotIn('secret-user', text)
        stage = self.home/'update'
        stage.mkdir()
        (stage/'result.txt').write_text('0', encoding='utf-8')
        core.atomic_json(self.home/'pending-update.json', {'stage':str(stage)})
        with patch('sys.stdout', io.StringIO()) as output:
            previous_result(self.home)
        self.assertIn('成功', output.getvalue())
        self.assertFalse((self.home/'pending-update.json').exists())

    def test_batch_import_continues_after_invalid_file(self):
        remote = self.home/'remote.db'
        core.store([self.row], remote)
        good = self.home/'good.snapshot.json'
        core.export(remote, 'remote', good)
        bad = self.home/'bad.snapshot.json'
        bad.write_text('{}', encoding='utf-8')
        with patch.object(core,'DEFAULT_LEDGER',self.ledger), patch('opencode_usage.menu.prompt',return_value='y'), patch('sys.stdout',io.StringIO()):
            result = menu.merge_files([bad,good], self.home, {})
        self.assertEqual((result['failed'],result['added']), (1,1))
        self.assertTrue(good.exists())

    def test_modern_import_preserves_names_without_prompt_or_alias(self):
        core.store([self.row], self.ledger, {'m': [
            {'id': 'mac', 'name': 'Mac 原名'}, {'id': 'other', 'name': '其他设备'}]})
        snapshot = self.home/'modern.json'
        core.export(self.ledger, 'mac', snapshot, 'Mac 原名')
        data = json.loads(snapshot.read_text(encoding='utf-8'))
        config = {'device_aliases': {'mac': '过期别名'}}
        with patch('opencode_usage.menu.prompt', side_effect=AssertionError('must not ask')):
            name = menu.import_device_name(data, config)
        self.assertIsNone(name)
        target = self.home/'target.db'
        menu.import_snapshot(snapshot, target, name)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute('SELECT device_id,device_name FROM message_sources ORDER BY device_id').fetchall(),
                             [('mac', 'Mac 原名'), ('other', '其他设备')])

    def test_legacy_import_explains_and_requests_source_name(self):
        config = {}
        with patch('opencode_usage.menu.prompt', return_value='旧电脑') as ask, patch('sys.stdout', io.StringIO()) as output:
            name = menu.import_device_name({'version': 1, 'device': 'old-id'}, config)
        self.assertEqual(name, '旧电脑')
        self.assertEqual(config['device_aliases'], {'old-id': '旧电脑'})
        self.assertIn('没有逐条设备来源', output.getvalue())
        ask.assert_called_once()

    def test_inbox_consumed_only_after_success_external_source_preserved(self):
        remote = self.home/'remote.db'
        core.store([self.row], remote)
        external = self.home/'external.snapshot.json'
        core.export(remote, 'remote', external)
        inbox = self.home/'imports'
        inbox.mkdir()
        pending = inbox/'pending.snapshot.json'
        pending.write_bytes(external.read_bytes())
        broken = inbox/'broken.snapshot.json'
        broken.write_text('{invalid', encoding='utf-8')
        config = {'db': str(self.home/'missing.db'), 'device_name': 'local'}
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('sys.stdout', io.StringIO()):
            menu.refresh(config, 'local', self.home, interactive=False, open_report=False)
        self.assertFalse(pending.exists())
        self.assertTrue(broken.exists())
        self.assertTrue(external.exists())
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 1)
        # An acknowledged duplicate dropped into the inbox is consumed as well.
        pending.write_bytes(external.read_bytes())
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('sys.stdout', io.StringIO()):
            menu.refresh(config, 'local', self.home, interactive=False, open_report=False)
        self.assertFalse(pending.exists())
        self.assertTrue(external.exists())

    def test_invalid_snapshot_and_missing_price_are_not_silently_accepted(self):
        path = self.home/'bad.json'
        path.write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            core.aggregate([path])
        from opencode_usage.dashboard import render
        core.store([dict(self.row, reasoning=4)], self.ledger)
        core.atomic_json(self.home/'report-settings.json', {'prices': [
            {'provider': 'p', 'model': 'model', 'effective_from': '1970-01-01', 'input': 1, 'output': 2}]})
        result = render(self.ledger, self.home/'price.html')
        self.assertTrue(result['rows'][0]['cost_missing'])

    def test_cli_report_keeps_conflict_warning(self):
        core.store([self.row], self.ledger)
        a,b = self.home/'a.json', self.home/'b.json'
        core.export(self.ledger, 'remote', a)
        data = json.loads(a.read_text(encoding='utf-8'))
        data['records'][0]['input'] += 1
        core.atomic_json(b, data)
        page = self.home/'conflicts.html'
        count, conflicts = core.report([a,b], page)
        self.assertEqual(count, 0)
        self.assertEqual(conflicts, ['m'])
        self.assertIn('消息冲突', page.with_suffix('.md').read_text(encoding='utf-8'))

    def test_dashboard_javascript_filters(self):
        import shutil
        import subprocess
        from opencode_usage.dashboard import render
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is optional for dashboard smoke tests')
        core.store([self.row], self.ledger, {'m': [{'id': 'device-a', 'name': '测试设备'}]})
        page = self.home/'dashboard.html'
        render(self.ledger, page)
        subprocess.run([node, str(Path(__file__).with_name('dashboard_smoke.cjs')), str(page)], check=True, capture_output=True)

    def test_schedule_never_registers_and_refuses_overwrite(self):
        from opencode_usage.automation import schedule
        output = self.home/'task.xml'
        result = schedule(output)
        self.assertFalse(result['registered'])
        self.assertIn('opencode_usage', output.read_text(encoding='utf-8'))
        import os
        if os.name == 'nt':
            # Task Scheduler COM receives a Unicode BSTR, not UTF-8 bytes.
            self.assertNotIn('<?xml', output.read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            schedule(output)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.ledger = self.home/'ledger.db'
        self.row = dict(zip(core.FIELDS, ('m', 's', 1000, 'p', 'model', 5, 2, 0, 0, 0, 0)))

    def test_unchanged_export_keeps_file_and_local_only_does_not_relay(self):
        core.store([self.row], self.ledger, {'m': [{'id': 'remote', 'name': '远端'}]})
        path = self.home/'full.json'
        core.export(self.ledger, 'local', path)
        original = path.read_bytes()
        core.export(self.ledger, 'local', path)
        self.assertEqual(path.read_bytes(), original)
        core.export(self.ledger, 'local', self.home/'local.json', local_only=True)
        self.assertEqual(json.loads((self.home/'local.json').read_text(encoding='utf-8'))['records'], [])
        self.assertEqual(len(json.loads(path.read_text(encoding='utf-8'))['records']), 1)

    def test_receive_ignores_timestamp_and_tracks_stale_devices(self):
        from opencode_usage.sync import receive, status
        core.store([self.row], self.ledger)
        path = self.home/'snapshot.json'
        core.export(self.ledger, 'remote', path)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['exported_at'] = '2000-01-01T00:00:00+00:00'
        receive(self.home, data, path)
        data['exported_at'] = '2001-01-01T00:00:00+00:00'
        self.assertTrue(receive(self.home, data, path, check_only=True))
        self.assertTrue(status(self.home)['devices']['remote']['stale'])

    def test_missing_local_db_still_imports_and_exports_without_browser(self):
        source = self.home/'remote.db'
        core.store([self.row], source, {'m': [{'id': 'remote', 'name': 'remote'}]})
        exchange_temp = tempfile.TemporaryDirectory()
        self.addCleanup(exchange_temp.cleanup)
        exchange = Path(exchange_temp.name)
        core.export(source, 'remote', exchange/'remote.snapshot.json')
        config = {'db': str(self.home/'missing.db'), 'exchange': str(exchange), 'device_name': 'local'}
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('sys.stdout', io.StringIO()), patch('opencode_usage.actions.open_path') as browser:
            result = menu.refresh(config, 'local', self.home, interactive=False, open_report=False)
        self.assertFalse(result['ok'])
        self.assertEqual(result['records'], 1)
        browser.assert_not_called()
        self.assertTrue((self.home/'reports/report.html').is_file())
        self.assertEqual(json.loads((exchange/'local.snapshot.json').read_text(encoding='utf-8'))['records'], [])

    def test_report_settings_apply_offset_and_effective_price(self):
        from opencode_usage.dashboard import render
        core.store([self.row], self.ledger)
        core.atomic_json(self.home/'report-settings.json', {
            'timezone_offset': 0, 'usd_to_cny': 7,
            'prices': [{'provider': 'p', 'model': 'model', 'effective_from': '1970-01-01', 'input': 2, 'output': 4}]})
        payload = render(self.ledger, self.home/'report.html')
        self.assertEqual(payload['rows'][0]['t'], '1970-01-01 00:00')
        self.assertAlmostEqual(payload['rows'][0]['cost'], 18/1e6)
        self.assertFalse(payload['rows'][0]['cost_missing'])
        self.assertEqual(payload['timezone'], 'UTC+0')

    def test_json_sync_no_prompt_and_partial_failure_exit(self):
        from argparse import Namespace
        from opencode_usage.automation import run
        core.atomic_json(self.home/'menu-config.json', {'device_name': 'local', 'db': str(self.home/'missing.db')})
        stream = io.StringIO()
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.automation.identity', return_value='local'), patch('builtins.input', side_effect=AssertionError('must not prompt')), patch('sys.stdout', stream):
            with self.assertRaises(SystemExit) as error:
                run(Namespace(command='sync', json=True, no_open=True))
        self.assertEqual(error.exception.code, 1)
        self.assertFalse(json.loads(stream.getvalue())['ok'])


if __name__ == '__main__':
    unittest.main()
