"""Optional real-browser integration tests using isolated sample data."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from opencode_usage import core, dashboard, menu, report_server
import test_sync


@unittest.skipUnless(sync_playwright and Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge').is_file(), 'Playwright and local Edge are optional')
class BrowserIntegrationTests(unittest.TestCase):
    def test_settings_refresh_and_maintenance(self):
        fixture = test_sync.SyncTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        home, ledger = fixture.home, fixture.ledger
        exchange = TemporaryDirectory()
        self.addCleanup(exchange.cleanup)
        core.atomic_json(home/'menu-config.json', {'device_name': 'local', 'check_updates': False})
        core.store([fixture.row], ledger)
        snapshot = home/'external.snapshot.json'
        core.export(ledger, 'local', snapshot, 'local')
        page_path = home/'reports'/'report.html'
        dashboard.render(ledger, page_path, local_sessions=[])
        with patch.object(core, 'DEFAULT_LEDGER', ledger), patch('opencode_usage.menu.identity', return_value='local'), patch('opencode_usage.menu.ensure_linux_path'), patch('opencode_usage.menu.prompt', side_effect=['v', '0']), patch('opencode_usage.actions.view_report') as view, patch('opencode_usage.sessions.list_sessions', return_value=[]):
            menu._main()
            callbacks = view.call_args.kwargs
            url = report_server.start(page_path, **callbacks)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path='/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.locator('#manage-status').get_by_text('点击会话改名', exact=False).wait_for()
                page.locator('#settings-open').click()
                page.wait_for_function("!document.querySelector('#settings-save').disabled")
                page.locator('#settings-dialog summary').get_by_text('同步设置').click()
                page.locator('#settings-dialog summary').get_by_text('数据与维护').click()
                shared = home/'shared-prices.json'
                core.atomic_json(shared, [])
                page.locator('#active-price-file').fill(str(shared))
                page.locator('#price-file-save').click()
                page.locator('.group-status').get_by_text('价格文件设置已保存', exact=False).wait_for()
                self.assertEqual(callbacks['settings']('settings-get', {})['active_price_file'], str(shared.resolve()))
                shared.unlink()
                page.locator('#active-price-file').fill('')
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#price-file-save').click()
                page.wait_for_function("!document.querySelector('#price-file-save').disabled")
                self.assertEqual(callbacks['settings']('settings-get', {})['active_price_file'], '')
                export = home/'web.snapshot.json'
                page.locator('#snapshot-path').fill(str(export))
                page.locator('#snapshot-metadata').uncheck()
                page.locator('#export-snapshot').click()
                page.locator('.group-status').get_by_text('快照已导出', exact=False).wait_for()
                self.assertEqual(core.read_snapshot(export)['sessions'], [])
                for width in (1440, 390):
                    page.set_viewport_size({'width': width, 'height': 900})
                    for field, button in (('#device-name', '#device-save'), ('#source-path', '#source-save'), ('#settings-rate', '#settings-save'), ('#workspace-path', '#open-workspace'), ('#workspace-path', '#migrate-workspace')):
                        field_box = page.locator(field).bounding_box()
                        button_box = page.locator(button).bounding_box()
                        self.assertAlmostEqual(field_box['y'] + field_box['height']/2, button_box['y'] + button_box['height']/2, delta=2, msg=f'{width}: {field} {field_box} {button_box}')
                        self.assertLessEqual(button_box['x'] + button_box['width'], width)
                page.set_viewport_size({'width': 1440, 'height': 900})
                self.assertEqual(page.locator('#workspace-path').input_value(), str(home.resolve()))
                self.assertEqual(page.locator('#device-save').inner_text(), '更新设备')
                self.assertEqual(page.locator('#source-save').inner_text(), '更新数据源')
                self.assertEqual(page.locator('#settings-save').inner_text(), '更新报告设置')
                self.assertEqual(page.locator('#sync-save').inner_text(), '更新同步设置')
                with patch('opencode_usage.actions.open_directory', return_value=True) as opener:
                    page.locator('#open-workspace').click()
                    page.locator('.group-status').get_by_text('已打开工作目录', exact=False).wait_for()
                    opener.assert_called_once_with(home)
                page.locator('#settings-timezone').fill('9')
                page.locator('#settings-rate').fill('7')
                page.locator('#settings-save').click()
                page.locator('.group-status').get_by_text('已更新', exact=False).wait_for()
                self.assertEqual(callbacks['settings']('settings-get', {})['timezone_offset'], 9)
                page.locator('[data-action="backup-create"]').click()
                page.locator('.group-status').get_by_text('备份已保存', exact=False).wait_for()
                page.get_by_text('换机请另存 ledger.db', exact=False).wait_for()
                page.locator('[data-action="export-csv"]').click()
                page.locator('.group-status').get_by_text('CSV 已导出', exact=False).wait_for()
                self.assertTrue((home/'reports'/'total.csv').is_file())
                page.locator('#sync-exchange').fill(exchange.name)
                page.locator('#sync-save').click()
                page.locator('.group-status').get_by_text('同步设置已保存', exact=False).wait_for()
                with patch('opencode_usage.report_server.choose_snapshot', return_value=str(snapshot)) as picker:
                    page.locator('#import-snapshot').click()
                    page.locator('#confirm-dialog').wait_for(state='visible')
                    page.locator('#confirm-accept').click()
                    page.locator('.group-status').get_by_text('导入完成', exact=False).wait_for()
                    picker.assert_called_once()
                    self.assertTrue(snapshot.exists())
                with patch('opencode_usage.menu.update.install', return_value=True) as install:
                    page.locator('#install-update').click()
                    page.locator('#confirm-dialog').wait_for(state='visible')
                    page.locator('#confirm-input').fill('2')
                    page.locator('#confirm-accept').click()
                    page.locator('.group-status').get_by_text('重新运行', exact=False).wait_for()
                    install.assert_called_once()
                    self.assertFalse(callbacks['active']())
                self.assertFalse(errors, errors)
                browser.close()
