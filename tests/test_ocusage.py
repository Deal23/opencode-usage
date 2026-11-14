import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
import subprocess
from unittest.mock import patch

from opencode_usage import core as ocusage
from opencode_usage import __version__
from opencode_usage.sync import import_snapshot, archive_snapshot
from opencode_usage.actions import show_report
from opencode_usage.dashboard import render, estimate, current_sessions


class UsageTests(unittest.TestCase):
    def test_refresh_opens_source_once_and_reuses_sessions(self):
        from opencode_usage import menu, dashboard
        import io
        home = self.dir/'refresh'
        home.mkdir()
        settings = {'db': str(self.db), 'device_name': 'test'}
        readonly = ocusage.readonly
        with patch.object(ocusage, 'DEFAULT_LEDGER', home/'ledger.db'), \
             patch.object(ocusage, 'readonly', wraps=readonly) as read, \
             patch.object(dashboard, 'current_sessions', wraps=dashboard.current_sessions) as sessions, \
             patch('opencode_usage.actions.open_path', return_value=True), \
             patch('sys.stdout', io.StringIO()):
            menu.refresh(settings, 'test', home)
            source_reads = [call for call in read.call_args_list if call.args[0] == self.db]
            self.assertEqual(len(source_reads), 1)
            sessions.assert_called_once()
        self.assertTrue((home/'reports/report.html').is_file())
        with closing(readonly(home/'ledger.db')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 1)

    def test_borrowed_source_connection_remains_open(self):
        with closing(ocusage.readonly(self.db)) as source:
            source.execute('BEGIN')
            self.assertEqual(len(ocusage.source_rows(source)), 1)
            self.assertTrue(source.in_transaction)
            self.assertIsNotNone(source.execute('SELECT 1').fetchone())

    def test_metadata_failure_marks_refresh_incomplete_but_preserves_usage(self):
        from opencode_usage import automation
        import io
        home = self.dir/'metadata-failure'
        home.mkdir()
        ledger = home/'ledger.db'
        config = {'db': str(self.db), 'device_name': 'test'}
        with patch.object(ocusage, 'DEFAULT_LEDGER', ledger), patch('opencode_usage.dashboard.refresh_metadata', side_effect=sqlite3.OperationalError('metadata failure')), patch('sys.stdout', io.StringIO()):
            result = automation.refresh(config, 'test', home, open_report=False)
        self.assertFalse(result['ok'])
        self.assertTrue(any('会话信息更新失败' in error for error in result['errors']))
        self.assertEqual(result['records'], 1)
        self.assertTrue((home/'reports/report.html').is_file())

    def test_menu_lock_failure_exits_nonzero(self):
        from opencode_usage import menu
        import io
        with patch('opencode_usage.locking.workspace_lock', side_effect=ValueError('already locked')), \
             patch('sys.stdout', io.StringIO()), \
             self.assertRaises(SystemExit) as error:
            menu.main()
        self.assertEqual(error.exception.code, 1)

    def test_first_collection_failure_skips_report_and_exchange_export(self):
        from opencode_usage import menu
        import io
        home = self.dir/'empty-workspace'
        home.mkdir()
        ocusage.atomic_json(home/'menu-config.json', {'device_name': 'test', 'db': str(self.db), 'check_updates': False, 'exchange': str(self.dir/'exchange')})
        with patch.object(ocusage, 'DEFAULT_LEDGER', home/'ledger.db'), \
             patch('opencode_usage.menu.identity', return_value='test'), \
             patch('opencode_usage.menu.ensure_linux_path'), \
             patch('builtins.input', side_effect=['1', '', '0']), \
             patch('opencode_usage.core.collect', side_effect=sqlite3.OperationalError('disk I/O error')), \
             patch('opencode_usage.menu.show_report') as report, \
             patch('opencode_usage.core.export') as export, \
             patch('sys.stdout', io.StringIO()) as output:
            menu.main()
            report.assert_not_called()
            export.assert_not_called()
            self.assertIn('本次源数据库', output.getvalue())
            self.assertIn('尚无可用台账', output.getvalue())

    def test_gbk_terminal_does_not_raise(self):
        import io
        from opencode_usage import terminal
        raw = io.BytesIO()
        output = io.TextIOWrapper(raw, encoding='gbk')
        with patch('sys.stdout', output):
            terminal.notice('已保存 😀', 'ok')
            terminal.menu('test', '设备 😀', self.dir)
            with patch('builtins.input', return_value='0'):
                self.assertEqual(terminal.prompt('选择 😀'), '0')
        output.flush()
        self.assertIn('已保存', raw.getvalue().decode('gbk'))

    def test_explicit_missing_database_never_falls_back(self):
        from opencode_usage.interaction import discover
        with self.assertRaisesRegex(ValueError, '不会自动切换'):
            discover({'db': str(self.dir/'missing.db')})

    def test_workspace_lock_excludes_other_process_and_releases(self):
        from opencode_usage.locking import workspace_lock
        code = ('from opencode_usage.locking import workspace_lock; '
                'from pathlib import Path; import sys; '
                'lock=workspace_lock(Path(sys.argv[1])); lock.__enter__()')
        with workspace_lock(self.dir):
            result = subprocess.run([sys.executable, '-c', code, str(self.dir)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        with workspace_lock(self.dir):
            pass

    def test_native_windows_open_failure_is_nonfatal(self):
        from opencode_usage import actions
        import io
        from types import SimpleNamespace
        page = self.dir/'report.html'
        page.write_text('report')
        fake_os = SimpleNamespace(name='nt', startfile=lambda _: (_ for _ in ()).throw(OSError('no association')))
        with patch('opencode_usage.actions.os', fake_os), patch('sys.stdout', io.StringIO()) as output:
            self.assertFalse(actions.open_path(page))
            self.assertIn('no association', output.getvalue())
            self.assertIn(str(page.resolve()), output.getvalue())

    def test_report_failure_preserves_ledger(self):
        from opencode_usage import menu
        import io
        with patch('opencode_usage.actions.generate_report', side_effect=OSError('disk full')), patch('sys.stdout', io.StringIO()) as output:
            menu.show_report('test')
            self.assertIn('报告生成未完成', output.getvalue())
            self.assertIn('台账未回退', output.getvalue())

    def test_shared_paths_translate_for_current_runtime(self):
        from opencode_usage.paths import local_path
        from unittest.mock import Mock
        from types import SimpleNamespace
        import os
        fake_os = SimpleNamespace(name='posix', path=os.path, environ=os.environ, uname=lambda: SimpleNamespace(release='microsoft-standard-WSL2'))
        with patch('opencode_usage.paths.os', fake_os), \
             patch('opencode_usage.paths.subprocess.run', return_value=Mock(stdout='/mnt/c/Users/test/db\n')) as run:
            self.assertEqual(str(local_path(r'C:\Users\test\db')).replace('\\', '/'), '/mnt/c/Users/test/db')
            self.assertEqual(run.call_args.args[0], ['wslpath', '-u', r'C:\Users\test\db'])
        with patch('opencode_usage.paths.os', SimpleNamespace(name='nt', path=os.path, environ=os.environ)):
            self.assertEqual(str(local_path('/mnt/c/Users/test/db')).replace('\\', '/'), 'C:/Users/test/db')

    def test_readonly_uses_snapshot_only_for_matching_io_error(self):
        error = sqlite3.OperationalError('disk I/O error')
        error.sqlite_errorcode = 4618
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.execute.side_effect = error
        snapshot = MagicMock()
        with patch.object(ocusage.sqlite3, 'connect', return_value=connection), \
             patch('opencode_usage.wsl_sqlite.needs_windows_snapshot', return_value=True), \
             patch('opencode_usage.wsl_sqlite.windows_snapshot', return_value=snapshot) as backup:
            self.assertIs(ocusage.readonly(self.db), snapshot)
            connection.close.assert_called_once()
            backup.assert_called_once_with(self.db.resolve())
        with patch.object(ocusage.sqlite3, 'connect', return_value=connection), \
             patch('opencode_usage.wsl_sqlite.needs_windows_snapshot', return_value=False), \
             patch('opencode_usage.wsl_sqlite.windows_snapshot') as backup:
            with self.assertRaises(sqlite3.OperationalError):
                ocusage.readonly(self.db)
            backup.assert_not_called()

    def test_snapshot_connection_cleans_up(self):
        from opencode_usage.wsl_sqlite import SnapshotConnection
        from unittest.mock import MagicMock
        connection = sqlite3.connect(':memory:', factory=SnapshotConnection)
        temporary = MagicMock()
        connection._snapshot = temporary
        connection.close()
        connection.close()
        temporary.cleanup.assert_called_once()

    def test_offline_help_topics(self):
        from opencode_usage.help import show
        import io
        output = io.StringIO()
        with patch('sys.stdout', output), patch('opencode_usage.help.prompt', side_effect=['2', '3', '1', '', '0', '0', '0']):
            show(self.dir)
        self.assertIn('云盘负责传输', output.getvalue())
        self.assertIn('工作目录', output.getvalue())
        with patch('sys.stdout', io.StringIO()), patch('opencode_usage.help.prompt', side_effect=['a', '', '0']):
            show(self.dir)
    def test_backup_retention_restore_and_preview(self):
        from opencode_usage import safety, actions
        home = self.dir/'safe'
        home.mkdir()
        ledger = home/'ledger.db'
        ocusage.collect(self.db, ledger)
        snap = self.dir/'preview.json'
        ocusage.export(ledger, 'linux', snap)
        info = actions.preview(snap, ledger)
        self.assertEqual((info['added'], info['same'], info['conflicts']), (0, 1, 0))
        first = safety.backup(home, 'test')
        with closing(sqlite3.connect(ledger)) as c, c:
            c.execute('UPDATE usage SET input=50')
        safety.restore(home, first)
        with closing(sqlite3.connect(ledger)) as c:
            self.assertEqual(c.execute('SELECT input FROM usage').fetchone()[0], 5)
        for _ in range(6):
            safety.backup(home, 'test')
        self.assertEqual(len(safety.backups(home)), 5)
    def test_identity_blocks_other_devices(self):
        from opencode_usage.interaction import identity
        with patch('pathlib.Path.home', return_value=self.dir):
            home = self.dir / 'work'
            home.mkdir()
            self.assertEqual(identity({'device': 'mac'}, home, lambda _: True), 'mac')
            with self.assertRaises(ValueError):
                identity({'device': 'windows'}, home, lambda _: True)
            owner = home / 'workspace-owner.json'
            owner.write_text('{"id":"foreign"}')
            with self.assertRaises(ValueError):
                identity({'device': 'mac'}, home, lambda _: True)

    def test_discover_and_change_summary(self):
        from opencode_usage.interaction import discover, changes
        self.assertEqual(discover({'db': str(self.db)}), self.db)
        self.assertEqual(changes({'a': (1,)}, {'a': (2,), 'b': (1,)}), (1, 1))
    def test_workspace_migration(self):
        from opencode_usage import workspace
        source = self.dir / 'workspace'
        source.mkdir()
        ocusage.collect(self.db, source / 'ledger.db')
        (source / 'report.md').write_text('report')
        dest = self.dir / 'moved'
        with patch.object(workspace, 'pointer', return_value=self.dir / 'pointer.json'):
            workspace.migrate(source, dest)
            self.assertEqual(workspace.current(), dest.resolve())
        self.assertTrue((source / 'ledger.db').exists())
        self.assertEqual((dest / 'report.md').read_text(), 'report')
        with closing(sqlite3.connect(dest / 'ledger.db')) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM usage').fetchone()[0], 1)
        with self.assertRaises(ValueError):
            workspace.migrate(source, dest)
    def test_direct_report_writes_markdown(self):
        ocusage.collect(self.db, self.ledger)
        snapshot = self.dir / "report.json"
        ocusage.export(self.ledger, "test", snapshot)
        output = self.dir / "direct.html"
        ocusage.report([snapshot], output)
        self.assertIn("每日 token", output.with_suffix(".md").read_text(encoding='utf-8'))

    def test_terminal_plain_text(self):
        from opencode_usage.terminal import menu
        import io
        stream = io.StringIO()
        with patch('sys.stdout', stream):
            menu('0.2.4', 'Linux', self.dir)
        self.assertIn('[3] 设置', stream.getvalue())
        self.assertNotIn('[4]', stream.getvalue())
        self.assertIn('[1] 刷新', stream.getvalue())
        self.assertNotIn('工作目录', stream.getvalue())
        self.assertNotIn('输入快照', stream.getvalue())
        self.assertNotIn('导出数据  ' + str(self.dir), stream.getvalue())
        self.assertNotIn('[d]', stream.getvalue())
        self.assertNotIn('\033', stream.getvalue())

    def test_terminal_wraps_chinese_and_long_paths(self):
        from opencode_usage.terminal import menu
        import io
        import os
        import unicodedata
        stream = io.StringIO()
        with patch('sys.stdout', stream), patch('opencode_usage.terminal.shutil.get_terminal_size', return_value=os.terminal_size((40, 24))):
            menu('0.2.5', '测试机器', '/very/long/path/' * 8)
        for line in stream.getvalue().splitlines():
            size = sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in line)
            self.assertLessEqual(size, 38)

    def test_linux_path_written_once(self):
        from opencode_usage import shell
        with patch.object(shell.sys, "platform", "linux"), patch.object(shell.Path, "home", return_value=self.dir), patch.dict(shell.os.environ, {"SHELL": "/bin/bash", "PATH": "/usr/bin"}):
            shell.ensure_linux_path()
            shell.ensure_linux_path()
        text = (self.dir / ".bashrc").read_text()
        self.assertEqual(text.count('export PATH="$HOME/.local/bin:$PATH"'), 1)

    def test_linux_path_notice_supports_cp1252(self):
        import io
        from opencode_usage import shell
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='cp1252', errors='strict')
        with patch.object(shell.sys, 'platform', 'linux'), \
             patch.object(shell.Path, 'home', return_value=self.dir), \
             patch.dict(shell.os.environ, {'SHELL': '/bin/bash', 'PATH': '/usr/bin'}), \
             patch('sys.stdout', stream):
            shell.ensure_linux_path()
        stream.flush()
        self.assertTrue(raw.getvalue())
        self.assertIn('source', raw.getvalue().decode('cp1252'))

    def test_archive_download_retry_and_traversal_rejection(self):
        from opencode_usage import update
        from unittest.mock import MagicMock
        import io
        import zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('repo/pyproject.toml', f'version = "{__version__}"\n')
        response = MagicMock()
        response.__enter__.return_value.read.side_effect = [data.getvalue(), b'']
        with patch.object(update, "urlopen", side_effect=[OSError('offline'), response]):
            self.assertTrue((update.download_source(self.dir) / 'pyproject.toml').is_file())
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('../escape.txt', 'bad')
        def bad_response(*args, **kwargs):
            r = MagicMock()
            r.__enter__.return_value.read.side_effect = [data.getvalue(), b'']
            return r
        with patch.object(update, 'urlopen', side_effect=bad_response):
            with self.assertRaises(OSError):
                update.download_source(self.dir)
        self.assertFalse((self.dir.parent / 'escape.txt').exists())

    def test_zero_token_sessions_excluded_from_ranking(self):
        ocusage.collect(self.db, self.ledger)
        with closing(sqlite3.connect(self.ledger)) as c, c:
            c.execute("UPDATE usage SET input=0, output=0")
        payload = render(self.ledger, self.dir / "zero.html")
        self.assertEqual(payload["sessions"], [])
        self.assertEqual(len(payload["rows"]), 1)

    def test_version_check_and_failed_network(self):
        from opencode_usage import update
        with patch.object(update, "urlopen") as fetch:
            fetch.return_value.__enter__.return_value.read.return_value = b'version = "99.0.0"\n'
            self.assertIn("发现新版本 99.0.0", update.check(force=True))
        with patch.object(update, "urlopen", side_effect=OSError("offline")):
            self.assertIn("暂无法检查", update.check(force=True))

    def test_update_failure_does_not_claim_success(self):
        from opencode_usage import update
        with patch.object(update, "download_source", return_value=self.dir), patch.object(update.subprocess, "run") as run:
            run.return_value.returncode = 1
            self.assertFalse(update.install())
            self.assertIn("wheel", run.call_args.args[0])

    def test_version_check_falls_back_to_api(self):
        from opencode_usage import update
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'version = "99.0.0"\n'
        with patch.object(update, "urlopen", side_effect=[OSError("DNS failure"), response]) as fetch:
            self.assertIn("发现新版本", update.check(force=True))
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(fetch.call_args.args[0].full_url, update.API_VERSION_URL)

    def test_device_provenance_survives_reexport(self):
        ocusage.collect(self.db, self.ledger, "linux-id", "Linux Server")
        snap = self.dir / "linux.json"
        ocusage.export(self.ledger, "linux-id", snap, "Linux Server")
        target = self.dir / "mac.sqlite"
        import_snapshot(snap, target)
        again = self.dir / "mac.json"
        ocusage.export(target, "mac-id", again, "Mac")
        data = json.loads(again.read_text())
        self.assertEqual(data["sources"]["m"], [{"id": "linux-id", "name": "Linux Server"}])
        payload = render(target, self.dir / "devices.html")
        self.assertEqual(payload["devices"][0]["name"], "Linux Server")
        self.assertEqual(payload["sessions"][0]["devices"], "Linux Server")
        self.assertEqual(payload["projects"][0]["devices"], "Linux Server")
        self.assertEqual(payload["rows"][0]["devices"], "Linux Server")

    def test_optional_private_metadata_and_legacy_device(self):
        from opencode_usage.dashboard import metadata_schema
        ocusage.collect(self.db, self.ledger)
        with closing(sqlite3.connect(self.ledger)) as c, c:
            metadata_schema(c)
            c.execute("INSERT INTO session_meta(session_id,title,project) VALUES ('s','Private title','/private/project')")
        snap = self.dir / "metadata.json"
        ocusage.export(self.ledger, "linux", snap)
        self.assertNotIn("Private title", snap.read_text())
        ocusage.export(self.ledger, "linux", snap, "Linux", include_metadata=True)
        target = self.dir / "metadata.sqlite"
        import_snapshot(snap, target)
        payload = render(target, self.dir / "metadata.html")
        self.assertEqual(payload["sessions"][0]["title"], "Private title")
        data = json.loads(snap.read_text())
        data["version"] = 1
        snap.write_text(json.dumps(data))
        import_snapshot(snap, target, "Old Linux")
        payload = render(target, self.dir / "legacy.html")
        self.assertEqual(payload["devices"][0]["name"], "Old Linux")

    def test_current_local_sessions(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("CREATE TABLE session_v2 (id,title,directory,model,parent_id,time_archived,time_suspended,time_updated,cost,tokens_input,tokens_output,tokens_reasoning,tokens_cache_read,tokens_cache_write)")
            c.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("s", "local session", "/project", '{"providerID":"p","id":"m"}', None, None, None, 1000, 1, 5, 2, 0, 0, 0))
        sessions = current_sessions(self.db)
        self.assertEqual(sessions[0]["总token"], 7)
        self.assertEqual(sessions[0]["状态"], "未归档")
        self.assertEqual(current_sessions(None), [])
        ocusage.collect(self.db, self.ledger)
        payload = render(self.ledger, self.dir / "local-device.html", db=self.db, device_name="K100")
        self.assertEqual(payload["current_sessions"][0]["设备"], "K100")

    def test_original_dashboard_payload_and_costs(self):
        ocusage.collect(self.db, self.ledger)
        page = self.dir / "dashboard.html"
        payload = render(self.ledger, page)
        self.assertEqual(payload["rows"][0]["tot"], 7)
        self.assertEqual(payload["rows"][0]["t"], "1970-01-01 08:00")
        self.assertIn("模型用量与花费分布", page.read_text(encoding='utf-8'))
        self.assertNotIn("@@DATA@@", page.read_text(encoding='utf-8'))
        md = page.with_suffix(".md").read_text(encoding='utf-8')
        self.assertIn("## 活跃时段", md)
        self.assertIn("## 本机现存会话", md)
        self.assertIn("近 30 天", md)
        row = dict(zip(ocusage.FIELDS, ("m", "s", 1000, "intern", "model", 5, 2, 0, 0, 0, 2)))
        self.assertEqual(estimate(row, {}), (0.0, False))

    def test_archived_import_and_total_report(self):
        ocusage.collect(self.db, self.ledger)
        snapshot = self.dir / "migration.json"
        ocusage.export(self.ledger, "mac", snapshot)
        archived = archive_snapshot(snapshot, self.dir)
        snapshot.unlink()
        self.assertTrue(archived.is_file())
        with patch.object(ocusage, "DEFAULT_LEDGER", self.ledger), patch("opencode_usage.actions.open_path", return_value=True):
            show_report("mac")
        self.assertTrue((self.dir / "reports/report.html").is_file())
        self.assertFalse((self.dir / "reports/total.csv").is_file())
        with patch.object(ocusage, 'DEFAULT_LEDGER', self.ledger):
            from opencode_usage.dashboard import render
            render(self.ledger, self.dir/'reports/report.html', local_sessions=[], csv_only=True)
        self.assertTrue((self.dir / "reports/total.csv").is_file())
        old_snapshot = self.dir / 'reports/total.snapshot.json'
        self.assertFalse(old_snapshot.exists())
        old_snapshot.write_text('user-owned existing export', encoding='utf-8')
        with patch.object(ocusage, 'DEFAULT_LEDGER', self.ledger), patch('opencode_usage.actions.open_path', return_value=True):
            show_report('mac')
        self.assertEqual(old_snapshot.read_text(encoding='utf-8'), 'user-owned existing export')

    def test_installed_cli(self):
        result = subprocess.run([sys.executable, "-m", "opencode_usage", "--version"], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), f"ocusage {__version__}")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "source db.sqlite"
        self.ledger = self.dir / "ledger.sqlite"
        default = patch.object(ocusage, "DEFAULT_LEDGER", self.ledger)
        default.start()
        self.addCleanup(default.stop)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, time_created INTEGER, type TEXT, data TEXT)")
            c.execute("INSERT INTO session_message VALUES (?,?,?,?,?)", ("m", "s", 1000, "assistant", json.dumps({"model": {"providerID": "p", "id": "m"}, "tokens": {"input": 5, "output": 2}})))
            c.execute("INSERT INTO session_message VALUES (?,?,?,?,?)", ("user", "s", 1000, "user", json.dumps({"text": "private"})))

    def test_collection_retains_deleted_and_snapshot_private(self):
        self.assertEqual(ocusage.collect(self.db, self.ledger), 1)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("DELETE FROM session_message")
        self.assertEqual(ocusage.collect(self.db, self.ledger), 0)
        snap = self.dir / "test.snapshot.json"
        self.assertEqual(ocusage.export(self.ledger, "mac", snap), 1)
        self.assertNotIn("private", snap.read_text())
        rows, conflicts = ocusage.aggregate([snap, snap])
        self.assertEqual((len(rows), conflicts), (1, []))

    def test_conflicting_snapshots_excluded(self):
        ocusage.collect(self.db, self.ledger)
        a, b = self.dir / "a.json", self.dir / "b.json"
        ocusage.export(self.ledger, "mac", a)
        data = json.loads(a.read_text())
        data["records"][0]["input"] += 1
        b.write_text(json.dumps(data))
        rows, conflicts = ocusage.aggregate([a, b])
        self.assertEqual(rows, [])
        self.assertEqual(conflicts, ["m"])

    def test_import_migration_and_conflict_rollback(self):
        ocusage.collect(self.db, self.ledger)
        snapshot = self.dir / "migration.json"
        ocusage.export(self.ledger, "mac", snapshot)
        target = self.dir / "target.sqlite"
        self.assertEqual(import_snapshot(snapshot, target), 1)
        self.assertEqual(import_snapshot(snapshot, target), 0)
        data = json.loads(snapshot.read_text())
        new = dict(data["records"][0], message_id="new")
        data["records"].insert(0, new)
        data["records"][1]["session_id"] = "conflicting-session"
        snapshot.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            import_snapshot(snapshot, target)
        with closing(sqlite3.connect(target)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM usage").fetchone()[0], 1)

    def test_local_and_import_usage_never_decrease(self):
        ocusage.collect(self.db, self.ledger)
        snapshot = self.dir / "usage.json"
        ocusage.export(self.ledger, "linux", snapshot)
        data = json.loads(snapshot.read_text())
        data["records"][0]["input"] = 20
        data["records"][0]["cost"] = 0.5
        snapshot.write_text(json.dumps(data))
        self.assertEqual(import_snapshot(snapshot, self.ledger), 0)
        ocusage.collect(self.db, self.ledger)
        data["records"][0]["input"] = 1
        data["records"][0]["cost"] = 0
        snapshot.write_text(json.dumps(data))
        import_snapshot(snapshot, self.ledger)
        with closing(sqlite3.connect(self.ledger)) as c:
            self.assertEqual(c.execute("SELECT input, cost FROM usage").fetchone(), (20, 0.5))

    def test_incompatible_change_rolls_back(self):
        ocusage.collect(self.db, self.ledger)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("UPDATE session_message SET session_id='other'")
        with self.assertRaises(ValueError):
            ocusage.collect(self.db, self.ledger)
        with closing(sqlite3.connect(self.ledger)) as c:
            self.assertEqual(c.execute("SELECT session_id FROM usage").fetchone()[0], "s")


if __name__ == "__main__":
    unittest.main()
