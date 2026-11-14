"""Validated, portable report settings; no credentials belong here."""
from datetime import timedelta, timezone
import json
import math
from pathlib import Path

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
        for key in ('input', 'output', 'reasoning', 'cache_read', 'cache_write'):
            rate = price.get(key)
            if rate is None:
                continue
            if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                raise ValueError(f'价格规则 {key} 无效')
    data['tz'] = timezone(timedelta(hours=data['timezone_offset']))
    data['label'] = f"UTC{data['timezone_offset']:+g}"
    return data
