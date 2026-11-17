import tempfile
import unittest
from pathlib import Path
from opencode_usage.reporting import initialize_prices
from opencode_usage.workspace import organize_prices, price_path


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

if __name__ == '__main__':
    unittest.main()
