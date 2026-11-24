"""Freeze reference costs per message; retry only records without a price."""
from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re

from . import core
from .workspace import price_path


def reverix_price(row, snapshot):
    price = snapshot.get('models', {}).get(row['model'])
    if not isinstance(price, dict):
        return None
    rate = dict(price)
    context = row['input'] + row['cache_read'] + row['cache_write']
    local = datetime.fromtimestamp(row['created_ms'] / 1000, timezone(timedelta(hours=8)))
    for tier in price.get('tiers', []):
        condition = tier.get('condition', '')
        match = re.fullmatch(r'上下文\s*(>|≥)\s*([\d,]+)', condition)
        peak = ('工作日高峰' in condition and local.weekday() < 5 and
                (9 <= local.hour < 12 or 14 <= local.hour < 18))
        if peak or (match and (context > int(match[2].replace(',', '')) if match[1] == '>'
                                else context >= int(match[2].replace(',', '')))):
            rate.update(tier)
    counts = {'input': row['input'], 'output': row['output'] + row['reasoning'],
              'cache_read': row['cache_read'], 'cache_write': row['cache_write']}
    if any(n and rate.get(k) is None for k, n in counts.items()):
        return None
    return sum(n * rate.get(k, 0) for k, n in counts.items()) / 1_000_000


def refresh(ledger, home, settings, providers):
    """Persist complete estimates, leaving unresolved records eligible for retry."""
    from .dashboard import estimate, configured_estimate
    path = price_path(home, 'reverix-prices.json')
    migrated = (Path(home)/'.state'/'reverix-price-migration.json').exists()
    snapshot = json.loads(path.read_text(encoding='utf-8')) if path.exists() and not migrated else {}
    with closing(core.connect(ledger)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS reference_costs (message_id TEXT PRIMARY KEY, cost_cny REAL NOT NULL, source TEXT NOT NULL, calculated_at TEXT NOT NULL)')
        query = 'SELECT ' + ','.join('u.' + k for k in core.FIELDS) + ' FROM usage u LEFT JOIN reference_costs r ON r.message_id=u.message_id WHERE r.message_id IS NULL'
        for values in db.execute(query).fetchall():
            row = dict(zip(core.FIELDS, values))
            if not any(row[k] for k in core.COUNTS):
                continue
            # Explicit user rules retain their existing precedence.
            date = datetime.fromtimestamp(row['created_ms'] / 1000, settings['tz']).date().isoformat()
            explicit = any(p['provider'] == row['provider'] and p['model'] == row['model'] and p['effective_from'] <= date for p in settings['prices'])
            configured = configured_estimate(row, providers, settings)
            price = reverix_price(row, snapshot) if configured is None and row['provider'] == 'reverix' and not explicit else None
            if configured is not None:
                amount, missing, source = configured, False, 'OpenCode 配置'
            elif price is not None:
                amount, missing, source = price, False, 'reverix-prices.json'
            elif row['provider'] == 'reverix' and row['model'] in snapshot.get('models', {}) and not explicit:
                # A partial price cannot safely price a record with additional token types.
                amount, missing, source = 0, True, 'reverix-prices.json'
            else:
                amount, missing = estimate(row, providers, settings)
                source = 'report rules / source cost'
            if not missing:
                db.execute('INSERT OR IGNORE INTO reference_costs VALUES (?,?,?,?)',
                           (row['message_id'], amount, source, datetime.now(timezone.utc).isoformat()))
