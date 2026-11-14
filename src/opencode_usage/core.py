"""Local-only OpenCode usage collection and snapshot aggregation."""
import argparse
from contextlib import closing, nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timezone

FIELDS = ("message_id", "session_id", "created_ms", "provider", "model", "input", "output", "reasoning", "cache_read", "cache_write", "cost")
COUNTS = ("input", "output", "reasoning", "cache_read", "cache_write")
DEFAULT_DB = Path.home() / ".local/share/opencode/opencode.db"
from .workspace import current
DEFAULT_LEDGER = current() / "ledger.db"


def connect(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS usage (message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, created_ms INTEGER NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, input INTEGER NOT NULL, output INTEGER NOT NULL, reasoning INTEGER NOT NULL, cache_read INTEGER NOT NULL, cache_write INTEGER NOT NULL, cost REAL NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS message_sources (message_id TEXT, device_id TEXT, device_name TEXT NOT NULL, PRIMARY KEY(message_id,device_id))")
    return c


def metadata_schema(c):
    c.execute("CREATE TABLE IF NOT EXISTS session_meta (session_id TEXT PRIMARY KEY, title TEXT, directory TEXT, project TEXT, duration_min REAL, time_created INTEGER, time_updated INTEGER, last_seen INTEGER)")


def compare(row, old):
    """Classify one validated row against a stored row without its message ID."""
    if old is None:
        return 'added'
    values = tuple(row[k] for k in FIELDS[1:])
    if old[:4] != values[:4]:
        return 'conflicts'
    return 'updated' if any(n > p for n, p in zip(values[4:], old[4:])) else 'same'


def readonly(path):
    from .paths import local_path
    path = local_path(path).resolve()
    if not path.is_file():
        raise ValueError(f"数据库不存在: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
        return connection
    except sqlite3.Error as exc:
        connection.close()
        from .wsl_sqlite import needs_windows_snapshot, windows_snapshot
        if needs_windows_snapshot(path, exc):
            return windows_snapshot(path)
        raise


def validate(row):
    if not isinstance(row, dict) or set(row) != set(FIELDS):
        raise ValueError("记录字段不符合快照格式")
    for field in ("message_id", "session_id", "provider", "model"):
        if not isinstance(row[field], str) or not row[field]:
            raise ValueError(f"无效字段: {field}")
    for field in ("created_ms", *COUNTS):
        if type(row[field]) is not int or row[field] < 0:
            raise ValueError(f"无效字段: {field}")
    import math
    if type(row["cost"]) not in (int, float) or not math.isfinite(row["cost"]) or row["cost"] < 0:
        raise ValueError("无效费用")
    return row


def source_rows(path):
    with source_connection(path) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables.intersection({"message", "session_message"}):
            raise ValueError("未识别的 OpenCode 数据库结构")
        rows = {}
        # V2 takes precedence over V1 for the same message.
        for table in ("message", "session_message"):
            if table not in tables:
                continue
            where = " WHERE type = 'assistant'" if table == "session_message" else " WHERE json_extract(data, '$.role') = 'assistant'"
            for mid, sid, created, raw in c.execute(f"SELECT id, session_id, time_created, data FROM {table}" + where):
                data = json.loads(raw)
                model = data.get("model") or {}
                tokens = data.get("tokens") or {}
                cache = tokens.get("cache") or {}
                row = dict(message_id=mid, session_id=sid, created_ms=(data.get("time") or {}).get("created") or created,
                           provider=data.get("providerID") or model.get("providerID") or "unknown",
                           model=data.get("modelID") or model.get("id") or "unknown",
                           input=tokens.get("input") or 0, output=tokens.get("output") or 0,
                           reasoning=tokens.get("reasoning") or 0, cache_read=cache.get("read") or 0,
                           cache_write=cache.get("write") or 0, cost=data.get("cost") or 0.0)
                rows[mid] = validate(row)
        return list(rows.values())


def source_connection(source):
    """Borrow an open source connection, or own a short-lived read-only one."""
    return nullcontext(source) if isinstance(source, sqlite3.Connection) else closing(readonly(source))


def store(rows, ledger, sources=None, connection=None):
    with (nullcontext(connection) if connection is not None else closing(connect(ledger))) as c, (nullcontext() if connection is not None else c):
        existing = {r[0]: r for r in c.execute("SELECT " + ",".join(FIELDS) + " FROM usage")}
        for row in rows:
            validate(row)
            old = existing.get(row["message_id"])
            values = tuple(row[k] for k in FIELDS)
            if compare(row, old[1:] if old else None) == 'conflicts':
                raise ValueError(f"消息存在不兼容变化，未写入: {row['message_id']}")
            if old:
                # Cumulative high-water marks: never reduce a token category or cost.
                values = values[:5] + tuple(max(values[i], old[i]) for i in range(5, 11))
            c.execute("INSERT OR REPLACE INTO usage VALUES (" + ",".join("?" for _ in FIELDS) + ")", values)
            existing[row["message_id"]] = values
        for mid, devices in (sources or {}).items():
            if mid not in existing:
                raise ValueError("来源关联了不存在的消息")
            for device in devices:
                if not isinstance(device, dict) or not all(isinstance(device.get(k), str) and device[k].strip() for k in ("id", "name")):
                    raise ValueError("设备来源字段无效")
                c.execute("INSERT INTO message_sources VALUES (?,?,?) ON CONFLICT(message_id,device_id) DO UPDATE SET device_name=excluded.device_name", (mid, device["id"], device["name"]))
    return len(rows)


def collect(db, ledger, device_id=None, device_name=None):
    rows = source_rows(db)
    sources = {r["message_id"]: [{"id": device_id, "name": device_name or device_id}] for r in rows} if device_id else None
    return store(rows, ledger, sources)


def import_legacy(path, ledger):
    with closing(readonly(path)) as c:
        rows = [validate(dict(zip(FIELDS, row))) for row in c.execute("SELECT " + ",".join(FIELDS) + " FROM usage")]
    return store(rows, ledger)


def atomic_json(path, data):
    from .files import atomic_output
    with atomic_output(Path(path).expanduser()) as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def content_hash(data):
    """Hash payload rather than transport timestamps; compatible with old snapshots."""
    payload = {k: v for k, v in data.items() if k not in {'exported_at', 'content_hash'}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def export(ledger, device, dest, device_name=None, include_metadata=False, local_only=False):
    if not device.strip():
        raise ValueError("设备名不能为空")
    with closing(readonly(ledger)) as c:
        rows = [dict(zip(FIELDS, r)) for r in c.execute("SELECT " + ",".join(FIELDS) + " FROM usage ORDER BY message_id")]
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sources = {}
        if "message_sources" in tables:
            for mid, did, name in c.execute("SELECT message_id,device_id,device_name FROM message_sources ORDER BY message_id,device_id"):
                sources.setdefault(mid, []).append({"id": did, "name": device_name if did == device and device_name else name})
        # Do not relabel unattributed historical messages as the exporting machine.
        sessions = [dict(zip(("session_id", "title", "project", "time_updated"), r)) for r in c.execute("SELECT session_id,title,project,time_updated FROM session_meta ORDER BY session_id")] if include_metadata and "session_meta" in tables else []
    if local_only:
        rows = [r for r in rows if any(d['id'] == device for d in sources.get(r['message_id'], []))]
        mids = {r['message_id'] for r in rows}
        sources = {mid: ds for mid, ds in sources.items() if mid in mids}
    sids = {r['session_id'] for r in rows}
    sessions = [s for s in sessions if s['session_id'] in sids]
    data = {"format": "ocusage-snapshot", "version": 2, "device": device,
                       "device_name": device_name or device, "sources": sources, "sessions": sessions,
                       "exported_at": datetime.now(timezone.utc).isoformat(), "records": rows}
    data['content_hash'] = content_hash(data)
    dest = Path(dest)
    if dest.is_file():
        try:
            if content_hash(json.loads(dest.read_text(encoding='utf-8'))) == data['content_hash']:
                return len(rows)
        except (OSError, ValueError, TypeError):
            pass
    atomic_json(dest, data)
    return len(rows)


def read_snapshot(path):
    """Read and validate one complete input before previewing or writing it."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format') != 'ocusage-snapshot' or type(data.get('version')) is not int or data['version'] not in (1, 2) or not isinstance(data.get('device'), str) or not data['device'].strip() or not isinstance(data.get('records'), list):
        raise ValueError(f'快照格式无效: {path}')
    mids = set()
    for row in data['records']:
        validate(row)
        if row['message_id'] in mids:
            raise ValueError(f"快照中消息 ID 重复: {row['message_id']}")
        mids.add(row['message_id'])
    sources = data.get('sources', {})
    if not isinstance(sources, dict):
        raise ValueError('设备来源格式无效')
    for mid, devices in sources.items():
        if mid not in mids or not isinstance(devices, list):
            raise ValueError('设备来源格式无效')
        for device in devices:
            if not isinstance(device, dict) or not all(isinstance(device.get(k), str) and device[k].strip() for k in ('id', 'name')):
                raise ValueError('设备来源字段无效')
    sessions = data.get('sessions', [])
    if not isinstance(sessions, list) or any(not isinstance(s, dict) or not isinstance(s.get('session_id'), str) or not all(isinstance(s.get(k), (str, type(None))) for k in ('title', 'project')) for s in sessions):
        raise ValueError('会话信息格式无效')
    if any(s.get('time_updated') is not None and (type(s['time_updated']) is not int or s['time_updated'] < 0) for s in sessions):
        raise ValueError('会话更新时间无效')
    return data


def aggregate(paths):
    return aggregate_snapshots(read_snapshot(path) for path in paths)


def aggregate_snapshots(snapshots):
    merged, conflicts = {}, set()
    for snapshot in snapshots:
        for row in snapshot['records']:
            mid = row["message_id"]
            if mid in merged and merged[mid] != row:
                conflicts.add(mid)
            else:
                merged[mid] = row
    # Never silently pick one version of a conflicting message.
    return [r for k, r in merged.items() if k not in conflicts], sorted(conflicts)


def report(paths, dest, include_csv=False):
    """Use the same report engine as the interactive workflow."""
    from . import dashboard
    import shutil
    snapshots = [read_snapshot(path) for path in paths]
    rows, conflicts = aggregate_snapshots(snapshots)
    valid = {r['message_id'] for r in rows}
    with tempfile.TemporaryDirectory(prefix='ocu-report-') as folder:
        ledger = Path(folder)/'ledger.db'
        from .sync import merge_snapshot
        store([], ledger)
        for data in snapshots:
            selected = [r for r in data['records'] if r['message_id'] in valid]
            sids = {r['session_id'] for r in selected}
            filtered = dict(data, records=selected,
                sources={mid: ds for mid, ds in data.get('sources', {}).items() if mid in valid},
                sessions=[s for s in data.get('sessions', []) if s['session_id'] in sids])
            merge_snapshot(filtered, ledger, data.get('device_name'))
        settings = DEFAULT_LEDGER.parent/'report-settings.json'
        if settings.is_file():
            shutil.copy2(settings, Path(folder)/settings.name)
        dashboard.render(ledger, dest, include_csv=include_csv, warnings=[f'存在 {len(conflicts)} 条消息冲突，已从本报告排除；请核对源快照。'] if conflicts else [])
    return len(rows), conflicts


def main():
    from .terminal import configure_output
    configure_output()
    p = argparse.ArgumentParser(description="本地 OpenCode 用量快照工具")
    from . import __version__
    p.add_argument("--version", action="version", version=f"ocusage {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ('sync', 'status', 'doctor', 'init', 'schedule'):
        command = sub.add_parser(name)
        command.add_argument('--json', action='store_true', help='输出 JSON，适合脚本')
        if name == 'sync':
            command.add_argument('--no-open', action='store_true', help='不打开浏览器')
        if name == 'init':
            command.add_argument('--device-name')
            command.add_argument('--db', type=Path)
            command.add_argument('--exchange', type=Path)
        if name == 'schedule':
            command.add_argument('--output', type=Path, required=True, help='生成任务文件，不注册任务')
    c = sub.add_parser("collect")
    c.add_argument("--db", type=Path, default=DEFAULT_DB)
    c.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    e = sub.add_parser("export")
    e.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    e.add_argument("--device", required=True)
    e.add_argument("--output", type=Path, required=True)
    e.add_argument('--usage-only', action='store_true', help='仅导出用量和来源，不含标题/项目；默认完整合并导出，含隐私')
    i = sub.add_parser("import-legacy", help="导入原 ocstats 台账（只读）")
    i.add_argument("source", type=Path)
    i.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    r = sub.add_parser("report")
    r.add_argument("snapshots", nargs="+", type=Path)
    r.add_argument("--output", type=Path, default=Path("report.html"))
    r.add_argument('--csv', action='store_true', help='额外导出用量及参考费用 CSV')
    a = p.parse_args()
    if a.command in {'sync', 'status', 'doctor', 'init', 'schedule'}:
        from .automation import run
        return run(a)
    try:
        from .locking import workspace_lock
        with workspace_lock(a.ledger.parent) if hasattr(a, 'ledger') else nullcontext():
            return _run_command(a)
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        p.exit(1, f"错误: {exc}\n")


def _run_command(a):
    if a.command == "collect":
        from .interaction import configuration
        from .interaction import identity
        config = configuration(a.ledger.parent)
        device = identity(config, a.ledger.parent, lambda _: False)
        print(f"采集 {collect(a.db, a.ledger, device, config.get('device_name', device))} 条；台账 {a.ledger}")
    elif a.command == "export":
        export(a.ledger, a.device, a.output, include_metadata=not a.usage_only)
        from .actions import export_summary
        print(export_summary(a.output, complete=not a.usage_only))
        print(f'快照 {a.output}')
    elif a.command == "import-legacy":
        print(f"导入 {import_legacy(a.source, a.ledger)} 条；台账 {a.ledger}")
    else:
        count, conflicts = report(a.snapshots, a.output, include_csv=a.csv)
        print(f"报表 {a.output}：{count} 条；冲突 {len(conflicts)} 条")
        for mid in conflicts:
            print(f"冲突消息: {mid}")
        if conflicts:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
