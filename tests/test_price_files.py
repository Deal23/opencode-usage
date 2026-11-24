import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from opencode_usage.reporting import initialize_prices
from opencode_usage.workspace import organize_prices, price_path
from opencode_usage.prices import sync_file
from opencode_usage import core, menu, prices, reporting


class PriceFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        patcher = patch('pathlib.Path.home', return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_initialize_preserves_user_file(self):
        path = initialize_prices(self.home)
        self.assertEqual(path, self.home/'prices/model-prices.json')
        path.write_text('[]', encoding='utf-8')
        self.assertEqual(initialize_prices(self.home).read_text(encoding='utf-8'), '[]')
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ['model-prices.json'])

    def test_legacy_price_files_migrate_without_losing_data(self):
        for name in ('model-prices.json', 'reverix-prices.json'):
            (self.home/name).write_text(name, encoding='utf-8')
            self.assertEqual(price_path(self.home, name), self.home/name)
        organize_prices(self.home)
        for name in ('model-prices.json', 'reverix-prices.json'):
            self.assertFalse((self.home/name).exists())
            self.assertEqual(price_path(self.home, name).read_text(encoding='utf-8'), name)

    def test_price_migration_conflict_preserves_both_files(self):
        (self.home/'model-prices.json').write_text('old', encoding='utf-8')
        (self.home/'reverix-prices.json').write_text('legacy', encoding='utf-8')
        (self.home/'prices').mkdir()
        (self.home/'prices/reverix-prices.json').write_text('new', encoding='utf-8')
        with self.assertRaises(ValueError):
            organize_prices(self.home)
        self.assertEqual((self.home/'model-prices.json').read_text(encoding='utf-8'), 'old')
        self.assertEqual((self.home/'reverix-prices.json').read_text(encoding='utf-8'), 'legacy')
        self.assertEqual((self.home/'prices/reverix-prices.json').read_text(encoding='utf-8'), 'new')

    def test_identical_legacy_file_is_archived_and_repeatable(self):
        (self.home/'prices').mkdir()
        (self.home/'model-prices.json').write_text('[]')
        (self.home/'prices/model-prices.json').write_text('[]')
        organize_prices(self.home)
        self.assertFalse((self.home/'model-prices.json').exists())
        backup, = (self.home/'backups/legacy-files').glob('model-prices.json.*.bak')
        self.assertEqual(backup.read_text(encoding='utf-8'), '[]')
        organize_prices(self.home)
        self.assertEqual((self.home/'prices/model-prices.json').read_text(encoding='utf-8'), '[]')

    def test_report_rules_migrated_with_backup_and_precedence(self):
        rule = dict(provider='p', model='m', effective_from='2026-01-01', input=1)
        other = dict(rule, input=2)
        (self.home/'report-settings.json').write_text(json.dumps({'prices': [rule], 'usd_to_cny': 7}))
        (self.home/'prices').mkdir()
        (self.home/'prices/model-prices.json').write_text(json.dumps([other]))
        result = initialize_prices(self.home)
        self.assertEqual(json.loads(result.read_text(encoding='utf-8'))[0], rule)
        migrated = json.loads(result.read_text(encoding='utf-8'))
        self.assertEqual(json.loads((self.home/'report-settings.json').read_text(encoding='utf-8'))['prices'], [])
        backup, = (self.home/'backups').glob('report-settings-before-price-migration-*.json')
        self.assertEqual(json.loads(backup.read_text(encoding='utf-8'))['prices'], [rule])
        previous, = (self.home/'backups').glob('model-prices-before-migration-*.json')
        self.assertEqual(json.loads(previous.read_text(encoding='utf-8')), [other])
        initialize_prices(self.home)
        self.assertEqual(json.loads(result.read_text(encoding='utf-8')), migrated)

    def test_sync_file_merge_conflict_and_missing(self):
        local = initialize_prices(self.home)
        shared = self.home/'shared.json'
        first = dict(provider='p', model='a', effective_from='2026-01-01', input=1)
        second = dict(provider='p', model='b', effective_from='2026-01-01', input=2)
        local.write_text(json.dumps([first]))
        shared.write_text(json.dumps([second]))
        self.assertEqual(sync_file(self.home, shared), 2)
        self.assertEqual(json.loads(local.read_text(encoding='utf-8')), [first, second])
        self.assertEqual(json.loads(shared.read_text(encoding='utf-8')), [first, second])
        shared.write_text(json.dumps([dict(first, input=3)]))
        before = local.read_bytes(), shared.read_bytes()
        with self.assertRaises(ValueError):
            sync_file(self.home, shared)
        self.assertEqual((local.read_bytes(), shared.read_bytes()), before)
        self.assertEqual(sync_file(self.home, shared, 'overwrite'), 2)
        self.assertEqual(json.loads(local.read_text(encoding='utf-8')), [dict(first, input=3), second])
        backup, = (self.home/'backups').glob('model-prices-before-sync-*.json')
        self.assertEqual(json.loads(backup.read_text(encoding='utf-8')), [first, second])
        shared.write_text(json.dumps([second]))
        self.assertEqual(sync_file(self.home, shared, 'replace'), 1)
        self.assertEqual(json.loads(local.read_text(encoding='utf-8')), [second])
        self.assertEqual(json.loads(shared.read_text(encoding='utf-8')), [second])
        backups = list((self.home/'backups').glob('model-prices-before-sync-*.json'))
        self.assertEqual(len(backups), 2)
        self.assertTrue(any(json.loads(p.read_text(encoding='utf-8')) == [dict(first, input=3), second] for p in backups))
        with self.assertRaises(ValueError):
            sync_file(self.home, self.home/'missing.json')

    def test_active_file_writes_and_missing_file_errors(self):
        initialize_prices(self.home)
        shared = self.home/'shared.json'
        shared.write_text('[]')
        core.atomic_json(self.home/'menu-config.json', {'active_price_file': str(shared)})
        rule = dict(provider='p', model='m', effective_from='2026-01-01', input=2)
        prices.save_rule(self.home, rule)
        self.assertEqual(json.loads(shared.read_text(encoding='utf-8')), [rule])
        self.assertIn(rule, reporting.report_settings(self.home)['prices'])
        shared.unlink()
        with self.assertRaisesRegex(ValueError, '不可用'):
            reporting.report_settings(self.home)

    def test_sync_detects_file_changed_during_merge(self):
        local = initialize_prices(self.home)
        local.write_text('[]')
        shared = self.home/'shared.json'
        shared.write_text('[]')
        original = Path.read_text
        count = 0
        def changed(path, *args, **kwargs):
            nonlocal count
            if path == shared:
                count += 1
                if count == 2:
                    path.write_text('[{"provider":"p","model":"m","effective_from":"2026-01-01","input":1}]')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', changed):
            with self.assertRaisesRegex(ValueError, '发生变化'):
                sync_file(self.home, shared)
        self.assertEqual(local.read_text(encoding='utf-8'), '[]')
        self.assertIn('"provider"', shared.read_text(encoding='utf-8'))

    def test_terminal_switch_back_preserves_shared_updates(self):
        initialize_prices(self.home)
        shared = self.home/'shared.json'
        shared.write_text('[]')
        config = {'active_price_file': str(shared)}
        rule = dict(provider='p', model='m', effective_from='2026-01-01', input=2)
        core.atomic_json(self.home/'menu-config.json', config)
        prices.save_rule(self.home, rule)
        with patch.object(menu, 'prompt', side_effect=['6', '-', '0']), patch.object(menu, 'notice'):
            menu.sync_preferences(self.home, config)
        self.assertNotIn('active_price_file', config)
        self.assertIn(rule, reporting.report_settings(self.home)['prices'])

    def test_terminal_offline_local_restore_requires_confirmation(self):
        initialize_prices(self.home)
        config = {'active_price_file': str(self.home/'missing.json')}
        core.atomic_json(self.home/'menu-config.json', config)
        with patch.object(menu, 'prompt', side_effect=['6', '-', 'n', '0']), patch.object(menu, 'notice'):
            menu.sync_preferences(self.home, config)
        self.assertIn('active_price_file', config)
        with patch.object(menu, 'prompt', side_effect=['6', '-', 'LOCAL', '0']), patch.object(menu, 'notice'):
            menu.sync_preferences(self.home, config)
        self.assertNotIn('active_price_file', config)
        self.assertIsInstance(reporting.report_settings(self.home)['prices'], list)

if __name__ == '__main__':
    unittest.main()
