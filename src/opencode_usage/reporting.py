"""Validated, portable report settings; no credentials belong here."""
from datetime import timedelta, timezone
import json
import math
from pathlib import Path
from .workspace import price_path

DEFAULTS = dict(timezone_offset=8, usd_to_cny=6.666,
                free_providers=['relay-ai', 'intern', 'paratera'], prices=[])


def settings(home):
    path = Path(home)/'report-settings.json'
    data = dict(DEFAULTS)
    if path.exists():
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('报表设置必须是 JSON 对象')
        data.update(value)
    return validate(data)


def report_settings(home):
    data = settings(home)
    path = price_path(home, 'model-prices.json')
    extra = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(extra, list):
        raise ValueError('价格文件必须是数组')
    # On identical dates explicit report settings take precedence.
    data['prices'] = data['prices'] + extra
    return validate(data)


def migrate_price_rules(home):
    """Consolidate legacy explicit rules while preserving every source file."""
    from . import core
    home = Path(home)
    path = price_path(home, 'model-prices.json')
    legacy = home/'report-settings.json'
    value = json.loads(legacy.read_text(encoding='utf-8')) if legacy.exists() else {}
    existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(value, dict) or not isinstance(existing, list):
        raise ValueError('价格配置格式无效，迁移未执行')
    old = value.get('prices', [])
    if not isinstance(old, list):
        raise ValueError('报表价格规则格式无效，迁移未执行')
    # Legacy report rules took precedence on identical effective dates.
    merged = list(old) + [rule for rule in existing if rule not in old]
    validate(dict(DEFAULTS, prices=merged))
    if old:
        from uuid import uuid4
        backup = home/'backups'/f'report-settings-before-price-migration-{uuid4().hex}.json'
        core.atomic_json(backup, value)
        core.atomic_json(path, merged)
        value['prices'] = []
        core.atomic_json(legacy, value)
    elif not path.exists():
        core.atomic_json(path, merged)
    return path


def initialize_prices(home):
    """Seed the editable workspace price file once without replacing user rules."""
    return migrate_price_rules(home)


def validate(data):
    """Validate a candidate before writing it, without changing caller data."""
    data = dict(data)
    for key, low, high in [('timezone_offset', -12, 14), ('usd_to_cny', 0.000001, 10000)]:
        if type(data[key]) not in (int, float) or not math.isfinite(data[key]) or not low <= data[key] <= high:
            raise ValueError(f'报表设置 {key} 无效')
    if not isinstance(data['free_providers'], list) or any(not isinstance(v, str) for v in data['free_providers']):
        raise ValueError('free_providers 必须是字符串数组')
    if not isinstance(data['prices'], list):
        raise ValueError('prices 必须是数组')
    from datetime import date
    for price in data['prices']:
        if not isinstance(price, dict) or any(not isinstance(price.get(k), str) for k in ('provider', 'model', 'effective_from')):
            raise ValueError('价格规则需要 provider、model 和 effective_from')
        date.fromisoformat(price['effective_from'])
        if price.get('currency', 'CNY') not in ('CNY', 'USD'):
            raise ValueError('价格规则 currency 必须是 CNY 或 USD')
        for key in ('input', 'output', 'reasoning', 'cache_read', 'cache_write'):
            rate = price.get(key)
            if rate is None:
                continue
            if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                raise ValueError(f'价格规则 {key} 无效')
    data['tz'] = timezone(timedelta(hours=data['timezone_offset']))
    data['label'] = f"UTC{data['timezone_offset']:+g}"
    return data
