import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opencode_usage import core, menu, prices


class PriceOverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.ledger = self.home/'ledger.db'

    def row(self, mid, provider, model, cost=0):
        return dict(zip(core.FIELDS, (mid, 'session', 1794960000000, provider, model, 100, 20, 0, 5, 0, cost)))

    def test_empty_and_recorded_models(self):
        self.assertIn('暂无台账', '\n'.join(prices.overview(self.home, 'local')))
        rows = [self.row('a', 'deepkey', 'gpt-6-luna'), self.row('b', 'unknown', 'missing'), self.row('c', 'intern', 'free'), self.row('d', 'remote', 'other'), self.row('e', 'unattributed', 'other')]
        sources = {mid: [{'id': 'local', 'name': '本机'}] for mid in ('a', 'b', 'c')}
        sources['d'] = [{'id': 'remote', 'name': '远端'}]
        core.store(rows, self.ledger, sources)
        core.atomic_json(self.home/'report-settings.json', {'usd_to_cny': 7})
        core.atomic_json(self.home/'model-prices.json', [dict(provider='deepkey', model='gpt-6-luna', effective_from='2026-01-01', currency='USD', input=0.1, output=0.5, cache_read=0.01)])
        text = '\n'.join(prices.overview(self.home, 'local'))
        self.assertIn('1.1  gpt-6-luna', text)
        self.assertIn('人民币元 / 百万 Token', text)
        self.assertIn('输入 ¥0.7000', text)
        self.assertIn('remote', text)
        self.assertIn('unattributed', text)
        self.assertIn('intern', text)
        self.assertIn('免费渠道', text)
        self.assertIn('missing', text)
        self.assertIn('缺价 1 条', text)

    def test_reverix_tiers_and_original_cost(self):
        core.store([self.row('a', 'reverix', 'model'), self.row('b', 'reverix', 'model', 0.2)], self.ledger, {mid: [{'id': 'local', 'name': '本机'}] for mid in ('a', 'b')})
        folder = self.home/'.config/opencode'
        folder.mkdir(parents=True)
        (folder/'opencode.json').write_text(json.dumps({'providers': {'reverix': {'models': {'model': {'cost': [{'input': 1, 'output': 2, 'cache': {'read': 0.1}}, {'tier': {'type': 'context', 'size': 1000}, 'input': 2, 'output': 4, 'cache': {'read': 0.2}}]}}}}}), encoding='utf-8')
        with patch('pathlib.Path.home', return_value=self.home):
            text = '\n'.join(prices.overview(self.home, 'local'))
        self.assertIn('基础价', text)
        self.assertIn('上下文 > 1,000', text)
        self.assertIn('输入 ¥6.6660', text)

    def test_data_menu_opens_prices_without_refresh(self):
        with patch('opencode_usage.menu.prompt', side_effect=['5', '', '0']), patch.object(menu, 'refresh') as refresh, patch('sys.stdout', io.StringIO()) as output:
            menu.data_menu(self.home, {}, 'test')
        self.assertIn('[5] 模型参考价格', output.getvalue())
        self.assertIn('暂无台账', output.getvalue())
        refresh.assert_not_called()

    def test_reverix_site_prices_apply_to_recorded_cost_and_include_sources(self):
        rows = [self.row('a', 'reverix', 'gpt-6.1-sol', 1), self.row('b', 'reverix', 'grok-4.7', 1), self.row('c', 'reverix', 'claude-opus-4-6', 1)]
        core.store(rows, self.ledger, {r['message_id']: [{'id': 'local', 'name': '本机'}] for r in rows})
        core.atomic_json(self.home/'reverix-prices.json', {'source': 'https://tipc.reverix.ai/pricing', 'retrieved': '2026-11-17', 'models': {'gpt-6.1-sol': {'input': 0.7, 'output': 3.5, 'tiers': [{'condition': '上下文 > 272,000', 'input': 1.4}]}, 'grok-4.7': {'input': 0.3, 'tiers': [{'condition': '上下文 ≥ 200,000', 'input': 0.6}]}}})
        text = '\n'.join(prices.overview(self.home, 'local'))
        self.assertIn('输入 ¥0.7000', text)
        self.assertIn('上下文 > 272,000', text)
        self.assertIn('上下文 ≥ 200,000', text)
        self.assertIn('claude-opus-4-6', text)
        self.assertIn('单价未配置', text)
        self.assertIn('── 1. reverix ─', text)
        self.assertIn('参考来源：', text)
        self.assertIn('https://tipc.reverix.ai/pricing', text)
        self.assertIn('消息原始费用', text)
        self.assertEqual(text.count('https://tipc.reverix.ai/pricing'), 1)

    def test_supplier_dividers_fill_width(self):
        from opencode_usage.textboard import cell_width
        rows = [self.row('a', 'deepkey', 'gpt-6-luna'), self.row('b', 'intern', 'free')]
        core.store(rows, self.ledger, {r['message_id']: [{'id': 'local', 'name': '本机'}] for r in rows})
        lines = prices.overview(self.home, 'local', width=60)
        headings = [line for line in lines if line.startswith('── ')]
        self.assertEqual(len(headings), 2)
        self.assertTrue(all(cell_width(line) == 60 for line in headings))

    def test_models_rank_by_usage_and_include_unattributed(self):
        low = self.row('a', 'deepkey', 'gpt-5.6-luna')
        high = self.row('b', 'deepkey', 'gpt-6-luna')
        high['input'] = 10000
        other = self.row('c', 'remote', 'other')
        core.store([low, high, other], self.ledger, {'a': [{'id': 'local', 'name': '本机'}], 'c': [{'id': 'remote', 'name': '远端'}]})
        text = '\n'.join(prices.overview(self.home, 'local'))
        self.assertIn('1.1  gpt-6-luna', text)
        self.assertIn('1.2  gpt-5.6-luna', text)
        self.assertIn('remote', text)


if __name__ == '__main__':
    unittest.main()
