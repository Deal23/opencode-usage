"""Content-addressed snapshot receipt state and local-device publication."""
import json
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone

from . import core
from .workspace import state_path, inbox


def import_snapshot(source, ledger, device_name=None):
    """Compatibility helper; interactive imports use accept to save receipts."""
    return merge_snapshot(core.read_snapshot(source), ledger, device_name)['added']


def archive_snapshot(source, home):
    """Validate and retain a normalized copy without modifying its source."""
    data = core.read_snapshot(source)
    dest = inbox(home)/f'{core.content_hash(data)}.snapshot.json'
    core.atomic_json(dest, data)
    return dest


def merge_snapshot(data, ledger, device_name=None):
    """Commit usage, provenance and metadata together; count actual changes."""
    rows = data['records']
    sources = data.get('sources', {})
    if data['version'] == 1:
        sources = {r['message_id']: [{'id': data['device'], 'name': device_name or data['device']}] for r in rows}
    result = dict(added=0, updated=0, same=0, metadata_updated=0)
    with closing(core.connect(ledger)) as c, c:
        core.metadata_schema(c)
        existing = {r[0]: r for r in c.execute('SELECT ' + ','.join(core.FIELDS) + ' FROM usage')}
        for row in rows:
            old = existing.get(row['message_id'])
            key = core.compare(row, old[1:] if old else None)
            if key == 'conflicts':
                raise ValueError(f"消息存在不兼容变化，未写入: {row['message_id']}")
            result[key] += 1
        core.store(rows, ledger, sources, connection=c)
        changed_sessions = set()
        for s in data.get('sessions', []):
            sid = s['session_id']
            old = c.execute('SELECT title,project,time_updated FROM session_meta WHERE session_id=?', (sid,)).fetchone()
            old = old or (None, None, None)
            newer = (s.get('time_updated') or 0) > (old[2] or 0)
            values = tuple(s.get(k) if s.get(k) and (not old[i] or newer) else old[i]
                           for i, k in enumerate(('title', 'project')))
            if values != old[:2]:
                changed_sessions.add(sid)
            c.execute('INSERT INTO session_meta(session_id,title,project,time_updated) VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET title=excluded.title,project=excluded.project,time_updated=excluded.time_updated',
                      (sid, *values, max(old[2] or 0, s.get('time_updated') or 0)))
        result['metadata_updated'] = len(changed_sessions)
    return result


def accept(home, source, data, ledger, device_name=None):
    """Shared manual/automatic import: retain retry input until receipt is saved."""
    archived = inbox(home)/f'{core.content_hash(data)}.snapshot.json'
    core.atomic_json(archived, data)
    result = merge_snapshot(data, ledger, device_name)
    receive(home, data, source)
    consume(home, archived)
    consume(home, source)
    return result


def load(home):
    path = state_path(home, 'sync-state.json')
    if not path.exists():
        return {'devices': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('devices'), dict):
        raise ValueError(f'同步状态格式无效：{path}')
    if any(not isinstance(value, dict) or not isinstance(value.get('recent_hashes', []), list)
           for value in data['devices'].values()):
        raise ValueError(f'同步设备状态格式无效：{path}')
    return data


def receive(home, data, source, check_only=False):
    state = load(home)
    digest = core.content_hash(data)
    device = data.get('device')
    if not isinstance(device, str) or not device.strip():
        raise ValueError('快照设备 ID 无效')
    previous = state['devices'].get(device, {})
    if check_only:
        return digest in previous.get('recent_hashes', [])
    hashes = previous.get('recent_hashes', [])
    hashes = [h for h in hashes if h != digest] + [digest]
    entry = dict(name=data.get('device_name') or device,
        exported_at=data.get('exported_at'), received_at=datetime.now(timezone.utc).isoformat(),
        content_hash=digest, records=len(data['records']), recent_hashes=hashes[-20:])
    if str(previous.get('exported_at') or '') > str(entry.get('exported_at') or ''):
        entry = dict(previous, recent_hashes=hashes[-20:])
    state['devices'][device] = entry
    core.atomic_json(state_path(home, 'sync-state.json'), state)


def consume(home, path):
    """Delete only an acknowledged local inbox item, never an external source."""
    path = Path(path)
    folders = (inbox(home), Path(home)/'imports')
    if not path.is_symlink() and any(not folder.is_symlink() and path.parent.resolve() == folder.resolve() for folder in folders):
        path.unlink(missing_ok=True)


def publish(home, ledger, device, config):
    exchange = Path(config['exchange'])
    validate_exchange(home, exchange)
    exchange.mkdir(parents=True, exist_ok=True)
    destination = exchange/f'{device}.snapshot.json'
    core.export(ledger, device, destination, config['device_name'], include_metadata=config.get('sync_metadata', False),
                local_only=not config.get('relay_history', False))
    state = load(home)
    data = json.loads(destination.read_text(encoding='utf-8'))
    state['published'] = dict(device=device, exported_at=data['exported_at'],
        checked_at=datetime.now(timezone.utc).isoformat(), content_hash=data['content_hash'],
        records=len(data['records']), transport='unknown')
    core.atomic_json(state_path(home, 'sync-state.json'), state)
    # Small independent heartbeat: unchanged usage snapshots need not be rewritten.
    last = state_path(home, 'collection-status.json')
    collection = json.loads(last.read_text(encoding='utf-8')) if last.exists() else {}
    core.atomic_json(exchange/f'{device}.status.json', dict(format='ocusage-status', version=1,
        device=device, name=config['device_name'], checked_at=state['published']['checked_at'],
        last_success=collection.get('last_success'), collection_ok=collection.get('ok'),
        content_hash=data['content_hash']))


def validate_exchange(home, exchange):
    home, exchange = Path(home).resolve(), Path(exchange).resolve()
    if home == exchange or home in exchange.parents or exchange in home.parents:
        raise ValueError('交换目录必须与本机工作目录分开，不能互相包含')
    return exchange


def read_heartbeats(home, exchange):
    state = load(home)
    for path in Path(exchange).glob('*.status.json'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            entry = state['devices'].get(data.get('device'))
            if data.get('format') != 'ocusage-status' or not entry:
                continue
            for key in ('checked_at', 'last_success'):
                value = data.get(key)
                if value:
                    datetime.fromisoformat(value)
            # Never let a heartbeat acknowledge a different usage snapshot.
            if entry.get('content_hash') == data.get('content_hash'):
                entry.update(checked_at=data.get('checked_at'), last_success=data.get('last_success'),
                             collection_ok=data.get('collection_ok'))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    core.atomic_json(state_path(home, 'sync-state.json'), state)


def status(home):
    data = load(home)
    now = datetime.now(timezone.utc)
    for device in data['devices'].values():
        try:
            age = (now - datetime.fromisoformat(device.get('checked_at') or device['exported_at'])).total_seconds()
            device['stale'] = age > 86400
        except (ValueError, TypeError, KeyError):
            device['stale'] = None
    return data
