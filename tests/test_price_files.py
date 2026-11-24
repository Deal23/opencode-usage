import tempfile
import json
import unittest
from pathlib import Path
from opencode_usage.reporting import initialize_prices
from opencode_usage.workspace import organize_prices, price_path
from opencode_usage.prices import sync_file


class PriceFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

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
        self.assertEqual((self.home/'model-prices.json').read_text(), 'old')
        self.assertEqual((self.home/'reverix-prices.json').read_text(), 'legacy')
        self.assertEqual((self.home/'prices/reverix-prices.json').read_text(), 'new')

    def test_report_rules_migrated_with_backup_and_precedence(self):
        rule = dict(provider='p', model='m', effective_from='2026-01-01', input=1)
        other = dict(rule, input=2)
        (self.home/'report-settings.json').write_text(json.dumps({'prices': [rule], 'usd_to_cny': 7}))
        (self.home/'prices').mkdir()
        (self.home/'prices/model-prices.json').write_text(json.dumps([other]))
        result = initialize_prices(self.home)
        self.assertEqual(json.loads(result.read_text()), [rule, other])
        self.assertEqual(json.loads((self.home/'report-settings.json').read_text())['prices'], [])
        backup, = (self.home/'backups').glob('report-settings-before-price-migration-*.json')
        self.assertEqual(json.loads(backup.read_text())['prices'], [rule])
        initialize_prices(self.home)
        self.assertEqual(json.loads(result.read_text()), [rule, other])

    def test_sync_file_merge_conflict_and_missing(self):
        local = initialize_prices(self.home)
        shared = self.home/'shared.json'
        first = dict(provider='p', model='a', effective_from='2026-01-01', input=1)
        second = dict(provider='p', model='b', effective_from='2026-01-01', input=2)
        local.write_text(json.dumps([first]))
        shared.write_text(json.dumps([second]))
        self.assertEqual(sync_file(self.home, shared), 2)
        self.assertEqual(json.loads(local.read_text()), [first, second])
        self.assertEqual(json.loads(shared.read_text()), [first, second])
        shared.write_text(json.dumps([dict(first, input=3)]))
        before = local.read_bytes(), shared.read_bytes()
        with self.assertRaises(ValueError):
            sync_file(self.home, shared)
        self.assertEqual((local.read_bytes(), shared.read_bytes()), before)
        self.assertEqual(sync_file(self.home, shared, 'overwrite'), 2)
        self.assertEqual(json.loads(local.read_text()), [dict(first, input=3), second])
        backup, = (self.home/'backups').glob('model-prices-before-sync-*.json')
        self.assertEqual(json.loads(backup.read_text()), [first, second])
        shared.write_text(json.dumps([second]))
        self.assertEqual(sync_file(self.home, shared, 'replace'), 1)
        self.assertEqual(json.loads(local.read_text()), [second])
        self.assertEqual(json.loads(shared.read_text()), [second])
        backups = list((self.home/'backups').glob('model-prices-before-sync-*.json'))
        self.assertEqual(len(backups), 2)
        self.assertTrue(any(json.loads(p.read_text()) == [dict(first, input=3), second] for p in backups))
        with self.assertRaises(ValueError):
            sync_file(self.home, self.home/'missing.json')

if __name__ == '__main__':
    unittest.main()
