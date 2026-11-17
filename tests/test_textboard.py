import io
import os
import subprocess
import tempfile
import unicodedata
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from opencode_usage import actions, core, dashboard, menu, textboard


class TextboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.page = self.home/'reports/report.html'
        self.path = self.page.with_suffix('.txt')
        self.ledger = self.home/'ledger.db'

    def payload(self):
        rows = []
        for i, day in enumerate(('2026-10-01', '2026-11-10', '2026-11-11', '2026-11-17', '2026-11-18')):
            created = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000)
            rows.append(dict(zip(core.FIELDS, (f'm{i}', f's{i}', created, 'intern', f'model{i}', 100, 20, 5, 50, 0, 0))))
        core.store(rows, self.ledger)
        return dashboard.render(self.ledger, self.page)

    def test_report_generates_text_snapshot_with_same_totals(self):
        payload = self.payload()
        self.assertTrue(self.path.is_file())
        text = self.path.read_text(encoding='utf-8')
        self.assertIn(f"总 Token  {sum(r['tot'] for r in payload['rows']):,}", text)
        self.assertIn('消息  5    会话  5    活跃  5 天', text)
        self.assertNotIn('请勿公开上传', text)
        self.assertNotIn('\x1b', text)
        self.assertTrue(self.page.with_suffix('.md').is_file())

    def test_periods_use_snapshot_date_not_last_record(self):
        payload = self.payload()
        text = textboard.render(payload, now=datetime(2026, 11, 17, 0, tzinfo=timezone.utc))
        self.assertRegex(text, r'今日\s+175\s+1\s+¥')
        self.assertRegex(text, r'近 7 天\s+350\s+2\s+¥')
        self.assertRegex(text, r'近 30 天\s+525\s+3\s+¥')
        trend = text.split('近 7 天趋势')[1].split('模型 Top')[0]
        self.assertIn('2026-11-11', trend)
        self.assertIn('2026-11-17', trend)
        self.assertNotIn('2026-11-18', trend)
        self.assertIn('2026-11-12         0  -', trend)
        self.assertIn('日均 50 · 最高 2026-11-11  175', trend)

    def test_periods_respect_report_timezone(self):
        payload = self.payload()
        payload.update(timezone_offset=-5, timezone='UTC-5')
        text = textboard.render(payload, now=datetime(2026, 11, 17, 2, tzinfo=timezone.utc))
        self.assertIn('2026-11-16 21:00  UTC-5', text)
        self.assertRegex(text, r'今日\s+0\s+0\s+¥')

    def test_empty_ledger_and_zero_totals(self):
        core.store([], self.ledger)
        payload = dashboard.render(self.ledger, self.page)
        text = textboard.render(payload)
        self.assertIn('暂无用量记录', text)
        self.assertIn('暂无模型用量', text)
        self.assertIn('Token  0', text)

    def test_recent_sessions_are_sorted_by_date_and_limited(self):
        payload = self.payload()
        payload['sessions'] = [dict(title=f'session-{i}', last=f'2026-11-{i:02d} 12:00', total=1, cost=0) for i in range(1, 8)]
        text = textboard.render(payload)
        self.assertLess(text.index('session-7'), text.index('session-6'))
        self.assertNotIn('session-2', text)

    def test_warnings_and_partial_cost_are_explicit(self):
        payload = self.payload()
        payload['rows'][0]['cost_missing'] = True
        payload['warnings'] = ['存在消息冲突']
        payload['sync'] = {'devices': {'remote': {'stale': True, 'collection_ok': False}}}
        text = textboard.render(payload)
        self.assertNotIn('部分缺价', text)
        self.assertNotIn('（缺价）', text)
        self.assertIn('缺价模型：', text)
        row = payload['rows'][0]
        self.assertIn(f"{row['prov']} / {row['model']}", text)
        self.assertIn('存在消息冲突', text)
        self.assertIn('1 台设备超过 24 小时', text)
        self.assertIn('1 台设备最近一次采集失败', text)

    def test_narrow_width_and_untrusted_metadata(self):
        payload = self.payload()
        payload['sessions'][0]['title'] = '中e\u0301' * 40 + '\x1b[31m\n伪造标题'
        text = textboard.render(payload, width=30)
        self.assertNotIn('\x1b', text)
        for line in text.splitlines():
            cells = sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in line)
            self.assertLessEqual(cells, 30)

    def test_columns_align_cjk_and_long_values(self):
        rows = [('范围', 'Token', '消息'), ('今日', '9', '1'), ('近 30 天', '1234567890', '2')]
        lines = textboard.columns(rows, (1, 2))
        for row, line in zip(rows, lines):
            self.assertTrue(line.startswith(row[0]))
            token_end = line.index(row[1], len(row[0])) + len(row[1])
            self.assertEqual(textboard.cell_width(line[:token_end]), 20)
            self.assertEqual(textboard.cell_width(line), 26)

    def test_wide_snapshot_and_narrow_display_reflow(self):
        payload = self.payload()
        payload['models'][0]['model'] = '模型' * 40
        textboard.write(payload, self.path)
        self.assertTrue(any(textboard.cell_width(line) > 76 for line in self.path.read_text(encoding='utf-8').splitlines()))
        with patch('sys.stdout', io.StringIO()) as output, \
             patch('sys.stdin.isatty', return_value=False), \
             patch('opencode_usage.actions.shutil.get_terminal_size', return_value=os.terminal_size((42, 24))):
            actions.view_textboard(self.home)
        self.assertTrue(all(textboard.cell_width(line) <= 40 for line in output.getvalue().splitlines()))

    def test_long_model_does_not_expand_other_rows_and_sessions_are_one_line(self):
        payload = self.payload()
        payload['models'][0]['model'] = '长模型' * 40
        payload['sessions'] = [dict(title='短会话', last='2026-11-17 12:00', total=175, cost=0)]
        text = textboard.render(payload)
        models = text.split('模型 Top 5')[1].split('最近会话')[0].splitlines()
        ordinary = next(line for line in models if 'model' in line)
        self.assertRegex(ordinary, r'^\d\.\s+')
        self.assertLess(textboard.cell_width(ordinary), 60)
        self.assertIn('1. 2026-11-17 12:00       175  ¥0.0000  短会话', text)

    def test_missing_models_are_deduplicated(self):
        payload = self.payload()
        for row in payload['rows'][:2]:
            row.update(prov='provider', model='same-model', cost_missing=True)
        payload['rows'][2].update(prov='provider', model='other-model', cost_missing=True)
        text = textboard.render(payload)
        status = text.split('── 数据状态 ──')[1]
        self.assertEqual(status.count('provider / same-model'), 1)
        self.assertEqual(status.count('provider / other-model'), 1)

    def test_missing_snapshot_does_not_generate_or_open_browser(self):
        with patch('sys.stdout', io.StringIO()) as output, \
             patch.object(actions, 'generate_report') as generate, \
             patch.object(actions, 'open_path') as open_path:
            actions.view_textboard(self.home)
        self.assertIn('选 1 刷新', output.getvalue())
        generate.assert_not_called()
        open_path.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_view_without_less_prints_snapshot_and_waits(self):
        self.payload()
        before = self.path.read_bytes()
        with patch('sys.stdout', io.StringIO()) as output, \
             patch.object(output, 'isatty', return_value=True), \
             patch('sys.stdin.isatty', return_value=True), \
             patch('opencode_usage.actions.shutil.which', return_value=None), \
             patch('opencode_usage.terminal.prompt', return_value='') as ask:
            actions.view_textboard(self.home)
        self.assertIn('OCU  /  用量报告', output.getvalue())
        self.assertIn('按回车返回主菜单', output.getvalue())
        ask.assert_called_once()
        self.assertEqual(self.path.read_bytes(), before)

    def test_pager_uses_safe_text_and_returns_to_menu(self):
        self.payload()
        with patch('sys.stdout', io.StringIO()) as output, \
             patch.object(output, 'isatty', return_value=True), \
             patch('sys.stdin.isatty', return_value=True), \
             patch('opencode_usage.actions.shutil.which', return_value='/usr/bin/less'), \
             patch.dict(os.environ, {'TERM': 'xterm'}), \
             patch('opencode_usage.actions.subprocess.run') as run:
            actions.view_textboard(self.home)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/less', '-X'])
        self.assertIn('按 q 返回主菜单', output.getvalue())
        self.assertIn('用量报告', run.call_args.kwargs['input'])
        self.assertEqual(run.call_args.kwargs['env']['LESSSECURE'], '1')

    def test_pager_failure_falls_back_to_text(self):
        self.payload()
        with patch('sys.stdout', io.StringIO()) as output, \
             patch.object(output, 'isatty', return_value=True), \
             patch('sys.stdin.isatty', return_value=True), \
             patch('opencode_usage.actions.shutil.which', return_value='less'), \
             patch.dict(os.environ, {'TERM': 'xterm'}), \
             patch('opencode_usage.actions.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'less')), \
             patch('opencode_usage.terminal.prompt', return_value=''):
            actions.view_textboard(self.home)
        self.assertIn('分页不可用', output.getvalue())
        self.assertIn('OCU  /  用量报告', output.getvalue())

    def test_pipe_prints_without_pager_or_prompt(self):
        self.payload()
        with patch('sys.stdout', io.StringIO()) as output, \
             patch('sys.stdin.isatty', return_value=False), \
             patch('opencode_usage.actions.subprocess.run') as run, \
             patch('opencode_usage.terminal.prompt') as ask:
            actions.view_textboard(self.home)
        self.assertIn('OCU  /  用量报告', output.getvalue())
        run.assert_not_called()
        ask.assert_not_called()

    def test_gbk_output(self):
        self.payload()
        raw = io.BytesIO()
        output = io.TextIOWrapper(raw, encoding='gbk')
        with patch('sys.stdout', output), patch('sys.stdin.isatty', return_value=False):
            actions.view_textboard(self.home)
        output.flush()
        self.assertIn('用量报告', raw.getvalue().decode('gbk'))

    def test_menu_t_does_not_refresh(self):
        core.atomic_json(self.home/'menu-config.json', {'device_name': 'local', 'check_updates': False})
        with patch.object(core, 'DEFAULT_LEDGER', self.ledger), \
             patch('opencode_usage.menu.identity', return_value='local'), \
             patch('opencode_usage.menu.ensure_linux_path'), \
             patch('opencode_usage.menu.prompt', side_effect=['T', '0']), \
             patch.object(menu, 'refresh') as refresh, \
             patch.object(actions, 'view_textboard') as view, \
             patch('sys.stdout', io.StringIO()) as output:
            menu._main()
        view.assert_called_once_with(self.home)
        refresh.assert_not_called()
        self.assertIn('[t] 文档报告', output.getvalue())
        self.assertIn('文档报告（分页按 q 返回）', output.getvalue())

    def test_csv_export_does_not_replace_text_snapshot(self):
        self.payload()
        self.path.write_text('saved snapshot', encoding='utf-8')
        dashboard.render(self.ledger, self.page, csv_only=True)
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'saved snapshot')


if __name__ == '__main__':
    unittest.main()
