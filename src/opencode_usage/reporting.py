"""Validated, portable report settings; no credentials belong here."""
from datetime import timedelta, timezone
import json
import math
from pathlib import Path
from .workspace import price_path, active_price_path

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
    path = active_price_path(home)
    extra = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(extra, list):
        raise ValueError('价格文件必须是数组')
    # On identical dates explicit report settings take precedence.
    data['prices'] = data['prices'] + extra
    data['opencode_prices_migrated'] = (Path(home)/'.state'/'opencode-price-migration.json').exists()
    data['deepseek_prices_migrated'] = (Path(home)/'.state'/'deepseek-price-migration.json').exists()
    return validate(data)


def migrate_price_rules(home):
    """Consolidate rules and remove legacy prices only after verified backup."""
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
    validate(dict(DEFAULTS, prices=old + existing))
    old_keys = {(p['provider'], p['model'], p['effective_from']) for p in old}
    merged = list(old) + [rule for rule in existing if (rule['provider'], rule['model'], rule['effective_from']) not in old_keys]
    builtin_marker = home/'.state'/'deepseek-price-migration.json'
    builtin = []
    if not builtin_marker.exists():
        from .price_migration import deepseek_rules
        keys = {(p['provider'], p['model'], p['effective_from']) for p in merged}
        builtin = [p for p in deepseek_rules() if (p['provider'], p['model'], p['effective_from']) not in keys]
        merged.extend(builtin)
    reverix = price_path(home, 'reverix-prices.json')
    marker = home/'.state'/'reverix-price-migration.json'
    converted = []
    reverix_original = None
    legacy_converted = []
    if reverix.exists():
        from .price_migration import convert_reverix
        reverix_original = reverix.read_bytes()
        legacy_converted = convert_reverix(json.loads(reverix_original.decode('utf-8')))
        # Existing explicit rules retain priority over legacy site prices.
        existing_keys = {(p.get('provider'), p.get('model'), p.get('effective_from')): p for p in merged}
        for rule in legacy_converted:
            key = (rule['provider'], rule['model'], rule['effective_from'])
            prior = existing_keys.get(key)
            comparable = {k: v for k, v in rule.items() if k != 'legacy_snapshot_metadata'}
            previous = {k: v for k, v in prior.items() if k != 'legacy_snapshot_metadata'} if prior else None
            if prior and previous != comparable:
                raise ValueError(f"旧价格迁移冲突：{' / '.join(key)}；请核对并保留两份价格文件")
        if marker.exists() and any((p['provider'], p['model'], p['effective_from']) not in existing_keys for p in legacy_converted):
            raise ValueError('旧价格已迁移但部分规则已移除；保留旧文件，请手动核对')
        converted += [p for p in legacy_converted if (p['provider'], p['model'], p['effective_from']) not in existing_keys]
        merged.extend(p for p in converted if (p['provider'], p['model'], p['effective_from']) not in existing_keys)
    opencode_file = Path.home()/'.config/opencode/opencode.json'
    opencode_marker = home/'.state'/'opencode-price-migration.json'
    imported = []
    opencode_ready = False
    if opencode_file.is_file() and not opencode_marker.exists():
        from datetime import datetime
        from .price_migration import convert_opencode
        try:
            config = json.loads(opencode_file.read_text(encoding='utf-8'))
            if not isinstance(config, dict) or not isinstance(config.get('providers', {}), dict):
                raise ValueError('供应商配置无效')
            # The old fallback only used OpenCode prices for Reverix providers.
            providers = {k: v for k, v in config.get('providers', {}).items() if k.startswith('reverix')}
            candidates = convert_opencode(providers, datetime.now(settings(home)['tz']).date().isoformat())
            existing_models = {(p.get('provider'), p.get('model')) for p in merged}
            imported = [p for p in candidates if (p['provider'], p['model']) not in existing_models]
            merged.extend(imported)
            opencode_ready = True
        except (ValueError, OSError, TypeError) as exc:
            from .terminal import notice
            notice(f'OpenCode 价格暂未迁移：{exc}；保留原配置，可稍后重试。', 'warn')
    validate(dict(DEFAULTS, prices=merged))
    if old or converted or imported or builtin:
        from uuid import uuid4
        if path.exists():
            backup = home/'backups'/f'model-prices-before-migration-{uuid4().hex}.json'
            core.atomic_json(backup, existing)
        if old:
            backup = home/'backups'/f'report-settings-before-price-migration-{uuid4().hex}.json'
            core.atomic_json(backup, value)
        core.atomic_json(path, merged)
    elif not path.exists():
        core.atomic_json(path, merged)
    # When a shared file is active, migration must reach the file actually used
    # for pricing before its completion markers are committed.
    active = active_price_path(home)
    if active.resolve() != path.resolve() and (old or converted or imported or builtin or reverix_original is not None):
        from .prices import sync_file
        sync_file(home, active, 'append')
    if old:
        value['prices'] = []
        core.atomic_json(legacy, value)
    if reverix_original is not None:
        from uuid import uuid4
        backup = home/'backups'/f'reverix-prices-before-migration-{uuid4().hex}.json'
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open('xb') as stream:
            stream.write(reverix_original)
        if reverix.read_bytes() != reverix_original:
            raise ValueError('旧价格文件在迁移期间发生变化，保留原文件')
        if not marker.exists():
            core.atomic_json(marker, {'completed': True})
        reverix.unlink()
    if opencode_ready:
        core.atomic_json(opencode_marker, {'completed': True})
    if not builtin_marker.exists():
        core.atomic_json(builtin_marker, {'completed': True})
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
        if price.get('pricing_type') == 'reverix_legacy':
            from .price_migration import validate_legacy_rule
            if price.get('currency', 'CNY') != 'CNY':
                raise ValueError('旧版 Reverix 规则必须使用 CNY')
            validate_legacy_rule(price)
        elif price.get('pricing_type') == 'deepseek_legacy':
            from datetime import date
            if price.get('currency') != 'CNY' or not isinstance(price.get('peak'), dict):
                raise ValueError('DeepSeek 峰谷价格无效')
            for key in ('window_start', 'window_end'):
                date.fromisoformat(price[key])
            if price['window_start'] > price['window_end'] or not isinstance(price.get('holidays'), list):
                raise ValueError('DeepSeek 峰谷日期无效')
            for day in price['holidays']:
                date.fromisoformat(day)
            for key in ('input', 'cache_read', 'output'):
                for rates in (price, price['peak']):
                    rate = rates.get(key)
                    if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                        raise ValueError('DeepSeek 峰谷单价无效')
        elif price.get('pricing_type') == 'opencode_context':
            from .price_migration import validate_legacy_rule
            import re
            if price.get('currency') != 'USD':
                raise ValueError('OpenCode 价格规则必须使用 USD')
            validate_legacy_rule(price)
            if any(not re.fullmatch(r'上下文\s*>\s*\d+', t['condition']) for t in price.get('tiers', [])):
                raise ValueError('OpenCode 价格分档条件无效')
        elif 'pricing_type' in price or 'tiers' in price:
            raise ValueError('价格规则类型或分档不受支持')
        for key in ('input', 'output', 'reasoning', 'cache_read', 'cache_write', 'audio_input', 'cache_write_1h'):
            rate = price.get(key)
            if rate is None:
                continue
            if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                raise ValueError(f'价格规则 {key} 无效')
    data['tz'] = timezone(timedelta(hours=data['timezone_offset']))
    data['label'] = f"UTC{data['timezone_offset']:+g}"
    return data
