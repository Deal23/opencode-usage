"""Lossless conversion helpers for legacy reference-price data."""
import re

from .reporting import DEFAULTS, validate


def deepseek_rules():
    """Represent the historical built-in tariff and its original boundaries."""
    rules = []
    for model, off, peak in [('deepseek-flash', (1, .02, 4), (2, .04, 8)),
                             ('deepseek-v4-pro', (4.5, .15, 13.5), (9, .30, 27))]:
        rules.append(dict(provider='deepseek', model=model, effective_from='0001-01-01',
                          currency='CNY', pricing_type='deepseek_legacy',
                          input=off[0], cache_read=off[1], output=off[2],
                          peak=dict(input=peak[0], cache_read=peak[1], output=peak[2]),
                          window_start='2026-09-25', window_end='2026-12-31',
                          holidays=['2026-09-25'] + [f'2026-10-{day:02d}' for day in range(1, 8)],
                          source='原内置 DeepSeek 历史参考规则'))
    return rules


def validate_legacy_rule(rule):
    """Reject malformed or unsupported conditions rather than ignoring them."""
    tiers = rule.get('tiers', [])
    if not isinstance(tiers, list):
        raise ValueError('价格分档必须是数组')
    for tier in tiers:
        if not isinstance(tier, dict) or set(tier) - {'condition', 'input', 'output', 'cache_read', 'cache_write'}:
            raise ValueError('价格分档字段无效')
        condition = tier.get('condition')
        if not isinstance(condition, str) or not (re.fullmatch(r'上下文\s*(>|≥)\s*\d[\d,]*', condition) or condition == '工作日高峰'):
            raise ValueError(f'无法识别价格分档：{condition}')
        for key in ('input', 'output', 'cache_read', 'cache_write'):
            rate = tier.get(key)
            if rate is not None:
                import math
                if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                    raise ValueError(f'价格分档 {key} 无效')


def convert_opencode(providers, effective_from):
    """Convert supported OpenCode model costs without assuming historical prices."""
    from datetime import date
    date.fromisoformat(effective_from)
    if not isinstance(providers, dict):
        raise ValueError('OpenCode 供应商配置无效')
    result = []
    for provider, info in providers.items():
        if not isinstance(info, dict) or not isinstance(info.get('models', {}), dict):
            raise ValueError(f'OpenCode 供应商配置无效：{provider}')
        for model, config in info.get('models', {}).items():
            if not isinstance(config, dict) or 'cost' not in config:
                continue
            costs = config['cost'] if isinstance(config['cost'], list) else [config['cost']]
            base, tiers = None, []
            for item in costs:
                if not isinstance(item, dict) or set(item) - {'input', 'output', 'cache', 'tier'}:
                    raise ValueError(f'无法安全迁移 OpenCode 价格：{provider}/{model}')
                cache = item.get('cache', {})
                if not isinstance(cache, dict) or set(cache) - {'read', 'write'}:
                    raise ValueError(f'无法安全迁移 OpenCode 缓存价格：{provider}/{model}')
                rates = {k: item[k] for k in ('input', 'output') if k in item}
                rates.update({f'cache_{k}': v for k, v in cache.items()})
                tier = item.get('tier')
                if tier is None:
                    if base is not None:
                        raise ValueError(f'OpenCode 价格存在多个基础价：{provider}/{model}')
                    base = rates
                elif isinstance(tier, dict) and set(tier) == {'type', 'size'} and tier['type'] == 'context' and type(tier['size']) is int and tier['size'] >= 0:
                    tiers.append(dict(rates, condition=f"上下文 > {tier['size']}"))
                else:
                    raise ValueError(f'无法安全迁移 OpenCode 价格分档：{provider}/{model}')
            if base is None:
                raise ValueError(f'OpenCode 价格缺少基础价：{provider}/{model}')
            rule = dict(provider=provider, model=model, effective_from=effective_from,
                        currency='USD', pricing_type='opencode_context', tiers=tiers,
                        source='OpenCode 配置', note='从迁移日生效，不用于推算历史价格。', **base)
            validate(dict(DEFAULTS, prices=[rule]))
            result.append(rule)
    return result


def convert_reverix(snapshot):
    """Convert legacy model prices without inventing a historical effective date.

    The old source had no effective dates; ``0001-01-01`` preserves the old
    behavior and the note makes this assumption visible to users.
    """
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('models'), dict):
        raise ValueError('旧版 Reverix 价格格式无效')
    result = []
    allowed = {'input', 'output', 'cache_read', 'cache_write', 'tiers'}
    for model, price in snapshot['models'].items():
        if not isinstance(model, str) or not model.strip() or not isinstance(price, dict):
            raise ValueError('旧版 Reverix 模型价格无效')
        if set(price) - allowed:
            raise ValueError(f'旧版价格包含无法安全迁移的字段：{model}')
        tiers = price.get('tiers', [])
        if not isinstance(tiers, list):
            raise ValueError(f'旧版价格分档无效：{model}')
        converted = []
        for tier in tiers:
            if not isinstance(tier, dict) or set(tier) - (allowed - {'tiers'} | {'condition'}):
                raise ValueError(f'旧版价格分档无效：{model}')
            condition = tier.get('condition')
            if not isinstance(condition, str) or not (re.fullmatch(r'上下文\s*(>|≥)\s*[\d,]+', condition) or condition == '工作日高峰'):
                raise ValueError(f'无法安全迁移价格分档：{model} · {condition}')
            converted.append(dict(tier))
        rule = dict(provider='reverix', model=model, effective_from='0001-01-01', currency='CNY',
                    pricing_type='reverix_legacy', source=snapshot.get('source', ''),
                    note='旧规则未指定生效日期；保留原有历史适用范围。')
        rule.update({k: v for k, v in price.items() if k != 'tiers'})
        rule['tiers'] = converted
        validate(dict(DEFAULTS, prices=[rule]))
        result.append(rule)
    return result
