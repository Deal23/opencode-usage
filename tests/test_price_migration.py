import unittest
import tempfile
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from opencode_usage.price_migration import convert_opencode, convert_reverix, deepseek_rules
from opencode_usage import core, dashboard, reporting
from opencode_usage.prices import sync_file


class PriceMigrationTests(unittest.TestCase):
    def setUp(self):
        isolated = tempfile.TemporaryDirectory()
        self.addCleanup(isolated.cleanup)
        home_patch = patch('pathlib.Path.home', return_value=Path(isolated.name))
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def test_deepseek_migrated_matches_builtin_boundaries(self):
        settings = reporting.validate(dict(reporting.DEFAULTS, prices=deepseek_rules()))
        legacy = reporting.validate(dict(reporting.DEFAULTS, prices=[]))
        row = dict(provider='deepseek', model='deepseek-flash', input=1000000, output=25,
                   reasoning=2, cache_read=50, cache_write=0, cost=0)
        for timestamp in ('2026-09-24T01:00:00+00:00', '2026-09-25T02:00:00+00:00',
                          '2026-10-08T00:59:00+00:00', '2026-10-08T01:00:00+00:00',
                          '2026-10-08T04:00:00+00:00', '2027-01-01T00:00:00+00:00'):
            row['created_ms'] = int(datetime.fromisoformat(timestamp).timestamp()*1000)
            with self.subTest(timestamp=timestamp):
                self.assertEqual(dashboard.estimate(row, {}, settings), dashboard.estimate(row, {}, legacy))

    def test_removed_default_does_not_reappear(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = reporting.initialize_prices(home)
            path.write_text('[]')
            reporting.initialize_prices(home)
            row = dict(provider='deepseek', model='deepseek-flash', created_ms=1790000000000,
                       input=1000000, output=0, reasoning=0, cache_read=0, cache_write=0, cost=0)
            self.assertTrue(dashboard.estimate(row, {}, reporting.report_settings(home))[1])

    def test_migrated_deepseek_respects_existing_free_provider(self):
        row = dict(provider='deepseek', model='deepseek-flash', created_ms=1790000000000,
                   input=1000000, output=0, reasoning=0, cache_read=0, cache_write=0, cost=0)
        settings = reporting.validate(dict(reporting.DEFAULTS, prices=deepseek_rules(), free_providers=['deepseek']))
        self.assertEqual(dashboard.estimate(row, {}, settings), (0.0, False))

    def test_workspace_copy_preserves_pricing_without_opencode_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, target = root/'source', root/'copy'
            source.mkdir()
            config = root/'.config/opencode/opencode.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'providers': {'reverix': {'models': {'m': {'cost': {'input': 2}}}}}}))
            with patch('pathlib.Path.home', return_value=root):
                reporting.initialize_prices(source)
                rule = next(p for p in reporting.report_settings(source)['prices'] if p['provider'] == 'reverix')
                row = dict(provider='reverix', model='m', created_ms=int(datetime.fromisoformat(rule['effective_from'] + 'T12:00:00+00:00').timestamp()*1000), input=1000000, output=0, reasoning=0, cache_read=0, cache_write=0, cost=0)
                original = dashboard.estimate(row, {}, reporting.report_settings(source))
                shutil.copytree(source, target)
                config.unlink()
                reporting.initialize_prices(target)
                self.assertEqual(dashboard.estimate(row, {}, reporting.report_settings(target)), original)

    def test_migration_updates_active_file_before_marking_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home/'shared.json'
            shared.write_text('[]')
            core.atomic_json(home/'menu-config.json', {'active_price_file': str(shared)})
            reporting.initialize_prices(home)
            self.assertEqual({p['model'] for p in json.loads(shared.read_text())}, {'deepseek-flash', 'deepseek-v4-pro'})
            self.assertTrue({'deepseek-flash', 'deepseek-v4-pro'} <= {p['model'] for p in reporting.report_settings(home)['prices']})
            self.assertTrue((home/'.state/deepseek-price-migration.json').exists())

    def test_shared_conflict_preserves_legacy_settings_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            rule = dict(provider='p', model='m', effective_from='2026-01-01', input=1)
            shared = home/'shared.json'
            core.atomic_json(shared, [dict(rule, input=2)])
            core.atomic_json(home/'menu-config.json', {'active_price_file': str(shared)})
            core.atomic_json(home/'report-settings.json', {'prices': [rule]})
            with self.assertRaisesRegex(ValueError, '冲突'):
                reporting.initialize_prices(home)
            self.assertEqual(json.loads((home/'report-settings.json').read_text())['prices'], [rule])
            self.assertEqual(json.loads(shared.read_text())[0]['input'], 2)
            core.atomic_json(shared, [])
            reporting.initialize_prices(home)
            self.assertIn(rule, json.loads(shared.read_text()))
            self.assertEqual(json.loads((home/'report-settings.json').read_text())['prices'], [])

    def test_opencode_conversion_uses_effective_date_and_context_tiers(self):
        providers = {'reverix': {'models': {'m': {'cost': [
            {'input': 1, 'output': 2, 'cache': {'read': .1}},
            {'input': 3, 'output': 4, 'tier': {'type': 'context', 'size': 200000}}]}}}}
        rule, = convert_opencode(providers, '2026-11-24')
        self.assertEqual(rule['effective_from'], '2026-11-24')
        self.assertEqual(rule['currency'], 'USD')
        self.assertEqual(rule['cache_read'], .1)
        self.assertEqual(rule['tiers'][0]['condition'], '上下文 > 200000')

    def test_opencode_conversion_rejects_unknown_cost_fields(self):
        with self.assertRaises(ValueError):
            convert_opencode({'p': {'models': {'m': {'cost': {'input': 1, 'unknown': 2}}}}}, '2026-11-24')

    def test_unsupported_opencode_config_does_not_block_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root/'workspace'
            home.mkdir()
            config = root/'.config/opencode/opencode.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'providers': {'reverix': {'models': {'m': {'cost': {'unknown': 1}}}}}}))
            with patch('pathlib.Path.home', return_value=root), patch('opencode_usage.terminal.notice') as notice:
                self.assertTrue(reporting.initialize_prices(home).exists())
                self.assertFalse((home/'.state/opencode-price-migration.json').exists())
                notice.assert_called()

    def test_opencode_converted_price_threshold_and_missing_rate(self):
        providers = {'p': {'models': {'m': {'cost': [{'input': 1, 'cache': {'read': .5}}, {'input': 2, 'tier': {'type': 'context', 'size': 100}}]}}}}
        rule, = convert_opencode(providers, '2026-01-01')
        settings = reporting.validate(dict(reporting.DEFAULTS, prices=[rule], usd_to_cny=1))
        row = dict(provider='p', model='m', created_ms=1790000000000, input=100, output=0, reasoning=0, cache_read=0, cache_write=0, cost=0)
        self.assertAlmostEqual(dashboard.estimate(row, {}, settings)[0], .0001)
        row['input'] = 101
        self.assertAlmostEqual(dashboard.estimate(row, {}, settings)[0], .000202)
        row['cache_read'] = 1
        self.assertTrue(dashboard.estimate(row, {}, settings)[1])

    def test_current_config_takes_precedence_and_falls_back(self):
        today = datetime.now(timezone.utc)
        row = dict(provider='p', model='m', created_ms=int(today.timestamp()*1000), input=1000000, output=0, reasoning=0, cache_read=0, cache_write=0, cost=0)
        settings = reporting.validate(dict(reporting.DEFAULTS, timezone_offset=0, usd_to_cny=1,
                              prices=[dict(provider='p', model='m', effective_from='0001-01-01', input=3)]))
        providers = {'p': {'models': {'m': {'cost': {'input': 2}}}}}
        self.assertEqual(dashboard.estimate(row, providers, settings), (2, False))
        providers['p']['models']['m']['cost'] = {'output': 1}
        self.assertEqual(dashboard.estimate(row, providers, settings), (3, False))
        row['created_ms'] = 0
        providers['p']['models']['m']['cost'] = {'input': 2}
        self.assertEqual(dashboard.estimate(row, providers, settings), (3, False))

    def test_unmigrated_reverix_never_backfills_current_config(self):
        row = dict(provider='reverix', model='m', created_ms=0, input=1000000,
                   output=0, reasoning=0, cache_read=0, cache_write=0, cost=0)
        settings = reporting.validate(dict(reporting.DEFAULTS, timezone_offset=0, usd_to_cny=1, prices=[]))
        providers = {'reverix': {'models': {'m': {'cost': {'input': 2}}}}}
        self.assertEqual(dashboard.estimate(row, providers, settings), (0.0, True))
        row['created_ms'] = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.assertEqual(dashboard.estimate(row, providers, settings), (2, False))
        settings['prices'] = [dict(provider='reverix', model='m', effective_from='0001-01-01', input=3)]
        providers['reverix']['models']['m']['cost'] = {'output': 1}
        self.assertEqual(dashboard.estimate(row, providers, settings), (3, False))

    def test_opencode_migrated_once_and_old_records_not_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root/'workspace'
            home.mkdir()
            config = root/'.config/opencode/opencode.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'providers': {'reverix': {'models': {'m': {'cost': {'input': 2}}}}}}))
            with patch('pathlib.Path.home', return_value=root):
                path = reporting.initialize_prices(home)
                rules = json.loads(path.read_text())
                imported = [rule for rule in rules if rule['provider'] == 'reverix']
                self.assertEqual(len(imported), 1)
                self.assertEqual(imported[0]['effective_from'], datetime.now().date().isoformat())
                row = dict(provider='reverix', model='m', created_ms=0, input=1000000, output=0, reasoning=0, cache_read=0, cache_write=0, cost=0)
                self.assertTrue(dashboard.estimate(row, {'reverix': {'models': {'m': {'cost': {'input': 2}}}}}, reporting.report_settings(home))[1])
                path.write_text('[]')
                reporting.initialize_prices(home)
                self.assertEqual(json.loads(path.read_text()), [])

    def test_reverix_preserves_rates_and_tiers(self):
        source = {'source': 'example', 'models': {'model': {'input': 1, 'output': 2,
                  'tiers': [{'condition': '上下文 ≥ 200,000', 'input': 3}]}}}
        rule, = convert_reverix(source)
        self.assertEqual(rule['tiers'], source['models']['model']['tiers'])
        self.assertEqual(rule['effective_from'], '0001-01-01')
        self.assertEqual(rule['pricing_type'], 'reverix_legacy')

    def test_unknown_condition_and_invalid_price_rejected(self):
        for tier in ({'condition': '其他时段', 'input': 1},
                     {'condition': '工作日高峰', 'input': -1},
                     {'condition': '工作日高峰', 'other': 2}):
            with self.subTest(tier=tier), self.assertRaises(ValueError):
                convert_reverix({'models': {'model': {'tiers': [tier]}}})

    def test_migrated_rule_pricing_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = {'models': {'model': {'input': 1, 'output': 2, 'tiers': [
                {'condition': '上下文 ≥ 200,000', 'input': 3}]}}}
            (home/'reverix-prices.json').write_text(json.dumps(source), encoding='utf-8')
            path = reporting.initialize_prices(home)
            rules = json.loads(path.read_text())
            row = dict(provider='reverix', model='model', created_ms=int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()*1000), input=200000, output=1, reasoning=0, cache_read=0, cache_write=0, cost=0)
            amount, missing = dashboard.estimate(row, {}, reporting.report_settings(home))
            self.assertFalse(missing)
            self.assertAlmostEqual(amount, .600002)
            reporting.initialize_prices(home)
            self.assertEqual(json.loads(path.read_text()), rules)
            self.assertTrue((home/'reverix-prices.json').exists())
            path.write_text('[]')
            reporting.initialize_prices(home)
            self.assertEqual(json.loads(path.read_text()), [])

    def test_explicit_rule_precedes_legacy_after_its_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home/'reverix-prices.json').write_text(json.dumps({'models': {'m': {'input': 1}}}))
            (home/'model-prices.json').write_text(json.dumps([dict(provider='reverix', model='m', effective_from='2026-06-01', input=2)]))
            reporting.initialize_prices(home)
            settings = reporting.report_settings(home)
            row = dict(provider='reverix', model='m', input=1000000, output=0, reasoning=0, cache_read=0, cache_write=0, cost=0)
            for date, expected in ((datetime(2026, 1, 1, tzinfo=timezone.utc), 1), (datetime(2026, 7, 1, tzinfo=timezone.utc), 2)):
                row['created_ms'] = int(date.timestamp()*1000)
                self.assertEqual(dashboard.estimate(row, {}, settings), (expected, False))

    def test_sync_rejects_invalid_tier_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            local = reporting.initialize_prices(home)
            shared = home/'shared.json'
            shared.write_text(json.dumps([dict(provider='reverix', model='m', effective_from='2026-01-01', pricing_type='reverix_legacy', tiers=[{'condition': 'unknown'}])]))
            before = (local.read_bytes(), shared.read_bytes())
            with self.assertRaises(ValueError):
                sync_file(home, shared)
            self.assertEqual((local.read_bytes(), shared.read_bytes()), before)

    def test_conflicting_legacy_price_is_not_silently_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy = home/'reverix-prices.json'
            local = home/'model-prices.json'
            legacy.write_text(json.dumps({'models': {'m': {'input': 1}}}))
            local.write_text(json.dumps([dict(provider='reverix', model='m', effective_from='0001-01-01', input=2)]))
            before = legacy.read_bytes(), local.read_bytes()
            with self.assertRaisesRegex(ValueError, '冲突'):
                reporting.initialize_prices(home)
            self.assertEqual((legacy.read_bytes(), local.read_bytes()), before)
            self.assertFalse((home/'.state'/'reverix-price-migration.json').exists())

    def test_retry_after_marker_write_failure_preserves_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home/'reverix-prices.json'
            old.write_text(json.dumps({'models': {'m': {'input': 1}}}))
            atomic = core.atomic_json
            def interrupted(path, data):
                if Path(path).name == 'reverix-price-migration.json':
                    raise OSError('simulated interruption')
                return atomic(path, data)
            with patch.object(core, 'atomic_json', side_effect=interrupted):
                with self.assertRaises(OSError):
                    reporting.initialize_prices(home)
            saved = json.loads((home/'prices/model-prices.json').read_text())
            self.assertEqual(sum(rule['provider'] == 'reverix' for rule in saved), 1)
            self.assertTrue(old.exists())
            reporting.initialize_prices(home)
            self.assertEqual(json.loads((home/'prices/model-prices.json').read_text()), saved)
            self.assertTrue((home/'.state/reverix-price-migration.json').is_file())
