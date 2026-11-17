import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opencode_usage import price_update
from opencode_usage.reporting import initialize_prices


class PriceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.rule = dict(provider='p', model='m', effective_from='2026-01-01', currency='USD', input=1, output=2, source='https://example.com/pricing')
        self.targets = [dict(provider='p', model='m')]

    def event(self, data):
        return json.dumps(dict(type='text', part=dict(text=json.dumps(data))))

    def test_initialize_preserves_user_file(self):
        path = initialize_prices(self.home)
        path.write_text('[]', encoding='utf-8')
        self.assertEqual(initialize_prices(self.home).read_text(encoding='utf-8'), '[]')

    def test_proposal_validates_and_save_preserves_other_rules(self):
        with patch('opencode_usage.price_update.subprocess.run', return_value=subprocess.CompletedProcess([], 0, self.event([self.rule]), '')):
            result = price_update.propose(self.home, 'p/model', self.targets)
        path = initialize_prices(self.home)
        before = json.loads(path.read_text(encoding='utf-8'))
        price_update.save(self.home, result)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved, before + [self.rule])

    def test_rejects_invalid_source_and_unknown_model(self):
        for rule in (dict(self.rule, source='http://example.com'), dict(self.rule, model='other')):
            with patch('opencode_usage.price_update.subprocess.run', return_value=subprocess.CompletedProcess([], 0, self.event([rule]), '')):
                with self.assertRaises(ValueError):
                    price_update.propose(self.home, 'p/model', self.targets)
        self.assertFalse((self.home/'model-prices.json').exists())


if __name__ == '__main__':
    unittest.main()
