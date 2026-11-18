import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from opencode_usage import core, reference_costs, reporting


class ReferenceCostsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.ledger = self.home / 'ledger.db'

    def row(self, mid, model='model', hour=2):
        stamp = int(datetime(2026, 11, 18, hour, tzinfo=timezone.utc).timestamp() * 1000)
        return dict(zip(core.FIELDS, (mid, 'session', stamp, 'reverix', model, 1000000, 0, 0, 0, 0, 0)))

    def snapshot(self, models):
        core.atomic_json(self.home / 'reverix-prices.json', {'models': models})

    def run_refresh(self):
        reference_costs.refresh(self.ledger, self.home, reporting.report_settings(self.home), {})

    def costs(self):
        with sqlite3.connect(self.ledger) as db:
            return dict(db.execute('SELECT message_id,cost_cny FROM reference_costs'))

    def test_freeze_and_retry_missing(self):
        core.store([self.row('priced'), self.row('missing', 'later')], self.ledger)
        self.snapshot({'model': {'input': 1}})
        self.run_refresh()
        self.assertEqual(self.costs(), {'priced': 1})
        self.snapshot({'model': {'input': 9}, 'later': {'input': 2}})
        self.run_refresh()
        self.assertEqual(self.costs(), {'priced': 1, 'missing': 2})

    def test_peak_and_boundary(self):
        price = {'input': 0.5, 'tiers': [{'condition': '工作日高峰', 'input': 1}]}
        self.assertEqual(reference_costs.reverix_price(self.row('peak', hour=2), {'models': {'model': price}}), 1)
        self.assertEqual(reference_costs.reverix_price(self.row('off', hour=4), {'models': {'model': price}}), 0.5)
        price = {'input': 1, 'tiers': [{'condition': '上下文 ≥ 200,000', 'input': 2}]}
        row = self.row('boundary')
        row['input'] = 200000
        self.assertAlmostEqual(reference_costs.reverix_price(row, {'models': {'model': price}}), 0.4)

    def test_partial_price_is_not_frozen(self):
        row = self.row('partial')
        row['cache_write'] = 1
        core.store([row], self.ledger)
        self.snapshot({'model': {'input': 1}})
        self.run_refresh()
        self.assertEqual(self.costs(), {})

    def test_report_uses_frozen_value_after_price_changes(self):
        from opencode_usage import dashboard
        core.store([self.row('priced')], self.ledger)
        self.snapshot({'model': {'input': 1}})
        self.run_refresh()
        self.snapshot({'model': {'input': 9}})
        payload = dashboard.render(self.ledger, self.home / 'report.html')
        self.assertEqual(payload['rows'][0]['cost'], 1)
        self.assertFalse(payload['rows'][0]['cost_missing'])

    def test_manual_price_preview_and_backfill(self):
        from opencode_usage import prices
        core.store([self.row('missing')], self.ledger)
        self.run_refresh()
        group = prices.missing_models(self.home)[0]
        self.assertEqual((group['messages'], group['tokens']), (1, 1000000))
        rule = dict(provider='reverix', model='model', effective_from='2026-11-18', currency='CNY', input=2)
        self.assertEqual(prices.preview_rule(self.home, rule), 1)
        prices.save_rule(self.home, rule)
        self.run_refresh()
        self.assertEqual(self.costs(), {'missing': 2})
        self.assertEqual(prices.missing_models(self.home), [])
        with self.assertRaises(ValueError):
            prices.save_rule(self.home, rule)

    def test_incomplete_manual_rule_is_not_free(self):
        from opencode_usage import prices
        row = self.row('missing')
        row['cache_write'] = 20
        core.store([row], self.ledger)
        self.run_refresh()
        rule = dict(provider='reverix', model='model', effective_from='2026-11-18', currency='CNY', input=2)
        self.assertEqual(prices.preview_rule(self.home, rule), 0)
        prices.save_rule(self.home, rule)
        self.run_refresh()
        self.assertEqual(self.costs(), {})

    def test_interactive_entry_saves_and_backfills(self):
        from opencode_usage import menu, prices
        core.store([self.row('missing')], self.ledger)
        self.run_refresh()
        with patch.object(menu, 'prompt', side_effect=['1', '1', '', '2', '', '', '', '', '', '', 'y']), patch.object(menu, 'show_report', return_value={}), patch.object(menu, 'notice'):
            menu.price_entry(self.home, 'device')
        self.assertEqual(self.costs(), {'missing': 2})
        self.assertEqual(prices.missing_models(self.home), [])

    def test_cancel_does_not_write_price(self):
        from opencode_usage import menu
        core.store([self.row('missing')], self.ledger)
        self.run_refresh()
        with patch.object(menu, 'prompt', side_effect=['1', '1', '', '2', '', '', '', '', '', '', 'n']), patch.object(menu, 'notice'):
            menu.price_entry(self.home, 'device')
        self.assertFalse((self.home / 'prices' / 'model-prices.json').exists())
        self.assertEqual(self.costs(), {})


if __name__ == '__main__':
    unittest.main()
