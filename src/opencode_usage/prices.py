"""Read-only model reference price overview for recorded usage."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path

from . import core
from .dashboard import estimate
from .reporting import report_settings
from .workspace import price_path


def overview(home, device, width=76):
    home = Path(home)
    guide = 'https://github.com/Deal23/opencode-usage/blob/main/docs/AI-PRICE-GUIDE.md'
    header = ['模型参考价格 · 台账中的模型',
              '更新价格：可请自己的 AI 按工作文档核实并更新；本工具不会自动调用 AI。',
              f'工作目录：{home}',
              f'AI 工作文档：{guide}',
              f"价格文件：{price_path(home, 'model-prices.json')}",
              '可告诉 AI：请按上述工作文档检查本工作目录的模型缺价并更新价格。']
    ledger = home/'ledger.db'
    if not ledger.is_file():
        return header + ['暂无台账，请先刷新或导入快照。']
    settings = report_settings(home)
    snapshot_path = price_path(home, 'reverix-prices.json')
    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8')) if snapshot_path.exists() else {'models': {}}
    try:
        providers = json.loads((Path.home()/'.config/opencode/opencode.json').read_text(encoding='utf-8')).get('providers', {})
    except (OSError, ValueError, AttributeError):
        providers = {}
    with closing(core.readonly(ledger)) as db:
        sql = 'SELECT ' + ','.join('u.' + field for field in core.FIELDS) + ' FROM usage u ORDER BY u.created_ms DESC'
        rows = [dict(zip(core.FIELDS, row)) for row in db.execute(sql)]
    groups = {}
    for row in rows:
        groups.setdefault((row['provider'], row['model']), []).append(row)
    lines = header + [f"单位：人民币元 / 百万 Token  ·  美元汇率 {settings['usd_to_cny']:g}",
                      '包含所有设备及无来源记录；按最近消息日期显示价格。']
    if not groups:
        return lines + ['暂无模型用量；请先刷新或导入。']
    sources = {}
    grouped_lines = {}
    def source(provider, value):
        sources.setdefault(provider, set()).add(value)
    ordered_providers = sorted({p for p, _ in groups}, key=lambda p: (-sum(sum(r[k] for k in core.COUNTS) for (prov, _), rs in groups.items() if prov == p for r in rs), p))
    supplier_numbers = {provider: index for index, provider in enumerate(ordered_providers, 1)}
    ordered = sorted(groups.items(), key=lambda item: (supplier_numbers[item[0][0]], -sum(sum(r[k] for k in core.COUNTS) for r in item[1]), item[0][1]))
    counts = {}
    for (provider, model), records in ordered:
        counts[provider] = counts.get(provider, 0) + 1
        lines = grouped_lines.setdefault(provider, [])
        latest = records[0]
        date = datetime.fromtimestamp(latest['created_ms']/1000, settings['tz']).date().isoformat()
        rules = [p for p in settings['prices'] if p['provider'] == provider and p['model'] == model and p['effective_from'] <= date]
        lines += ['', f'{supplier_numbers[provider]}.{counts[provider]}  {model}']
        if rules:
            rule = max(rules, key=lambda p: p['effective_from'])
            source(provider, rule.get('source') or '本地价格配置')
            fx = settings['usd_to_cny'] if rule.get('currency', 'CNY') == 'USD' else 1
            labels = [('输入', 'input'), ('输出', 'output'), ('推理', 'reasoning'), ('缓存读', 'cache_read'), ('缓存写', 'cache_write')]
            lines += ['  ' + '  ·  '.join(f"{label} ¥{rule[key]*fx:.4f}" if rule.get(key) is not None else f'{label} —' for label, key in labels)]
        elif provider in settings['free_providers']:
            source(provider, '报表免费渠道配置')
            lines += ['  免费渠道 · 输入、输出及缓存 ¥0']
        elif provider == 'deepseek' and model in ('deepseek-flash', 'deepseek-v4-pro'):
            source(provider, '内置 DeepSeek 历史参考规则')
            peak, off = ((2, .04, 8), (1, .02, 4)) if model == 'deepseek-flash' else ((9, .30, 27), (4.5, .15, 13.5))
            lines += [f'  峰时  输入 ¥{peak[0]:.4f}  ·  缓存读 ¥{peak[1]:.4f}  ·  输出 ¥{peak[2]:.4f}',
                      f'  谷时  输入 ¥{off[0]:.4f}  ·  缓存读 ¥{off[1]:.4f}  ·  输出 ¥{off[2]:.4f}',
                      '  缓存写 —']
        elif provider == 'reverix' and model in snapshot['models']:
            price = snapshot['models'][model]
            source(provider, snapshot['source'] + '（' + snapshot['retrieved'] + '）')
            for label, rate in [('基础价', price), *[(tier['condition'], tier) for tier in price.get('tiers', [])]]:
                values = [('输入', 'input'), ('输出/推理', 'output'), ('缓存读', 'cache_read'), ('缓存写', 'cache_write')]
                lines += [f'  {label}  ·  ' + '  ·  '.join(f'{name} ¥{rate[key]:.4f}' if key in rate else f'{name} —' for name, key in values)]
        elif provider.startswith('reverix'):
            model_config = providers.get(provider, {}).get('models', {}).get(model, {})
            rates = model_config.get('cost') if isinstance(model_config, dict) else None
            rates = [rates] if isinstance(rates, dict) else rates if isinstance(rates, list) else []
            source(provider, '~/.config/opencode/opencode.json' if rates else '消息原始费用')
            for rate in rates:
                if not isinstance(rate, dict):
                    continue
                cache = rate.get('cache') if isinstance(rate.get('cache'), dict) else {}
                tier = rate.get('tier')
                label = f"上下文 > {tier['size']:,}" if isinstance(tier, dict) and tier.get('type') == 'context' and isinstance(tier.get('size'), (int, float)) else '基础价'
                values = [('输入', rate.get('input')), ('输出/推理', rate.get('output')), ('缓存读', cache.get('read')), ('缓存写', cache.get('write'))]
                lines += [f"  {label}  ·  " + '  ·  '.join(f'{key} ¥{value*settings["usd_to_cny"]:.4f}' if isinstance(value, (int, float)) else f'{key} —' for key, value in values)]
            if not rates:
                lines += ['  单价未配置 · 使用原始费用']
        else:
            source(provider, '消息原始费用')
            lines += ['  单价未配置 · 使用原始费用']
        missing = sum(estimate(r, providers, settings)[1] for r in records)
        if missing:
            lines += [f'  缺价 {missing:,} 条']
    result = header + [f"单位：人民币元 / 百万 Token  ·  美元汇率 {settings['usd_to_cny']:g}",
                       '包含所有设备及无来源记录；按最近消息日期显示价格。']
    from .textboard import cell_width
    for provider, entries in grouped_lines.items():
        title = f'── {supplier_numbers[provider]}. {provider} '
        heading = title + '─' * max(2, min(120, max(20, width)) - cell_width(title))
        result += ['', heading, *entries,
                   '  参考来源：' + ' · '.join(sorted(sources.get(provider, [])))]
    return result
