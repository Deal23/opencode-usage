"""Shared refresh and non-interactive commands; never request input implicitly."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
from xml.sax.saxutils import escape

from . import core
from .interaction import configuration, discover, identity
from .locking import workspace_lock
from .workspace import state_path, organize_state, inbox


def diagnose(home, config):
    checks = []
    try:
        db = discover(config)
        if db is None:
            raise ValueError('未找到数据库；请用 init --db PATH 设置')
        with core.source_connection(db) as source:
            core.source_rows(source)
        checks.append(dict(name='source', ok=True, path=str(db)))
    except (OSError, ValueError, sqlite3.Error) as exc:
        checks.append(dict(name='source', ok=False, error=str(exc)))
    ledger = home/'ledger.db'
    if ledger.exists():
        with core.source_connection(ledger) as db:
            checks.append(dict(name='ledger', ok=db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'))
    if config.get('exchange'):
        folder = Path(config['exchange'])
        checks.append(dict(name='exchange', ok=folder.is_dir() and os.access(folder, os.W_OK),
                           path=str(folder), transport='unknown'))
    return dict(ok=all(c['ok'] for c in checks), checks=checks,
                python=sys.version.split()[0], platform=sys.platform,
                workspace=str(home), encoding=sys.stdout.encoding)


def diagnostic_bundle(home, config):
    """Allowlist fields instead of attempting to redact arbitrary exception text."""
    from . import __version__
    data = diagnose(home, config)
    safe = dict(version=__version__, python=data['python'], platform=data['platform'],
        encoding=data['encoding'], checks=[{'name': c['name'], 'ok': c['ok']} for c in data['checks']])
    last = state_path(home, 'last-sync.json')
    if last.exists():
        value = json.loads(last.read_text(encoding='utf-8'))
        safe['last_sync'] = {key: value.get(key) for key in ('ok', 'records', 'at')}
        safe['last_sync']['failed_stages'] = sorted({str(e).split(':', 1)[0].split(' ', 1)[0] for e in value.get('errors', [])} & {'collect', 'import', 'export', 'report'})
    path = state_path(home, 'diagnostics.json')
    core.atomic_json(path, safe)
    return path


def schedule(output):
    """Generate opt-in task templates with the exact interpreter, no PATH dependency."""
    import subprocess
    command = ([sys.executable] if getattr(sys, 'frozen', False) else
               [sys.executable, '-m', 'opencode_usage']) + ['sync', '--no-open', '--json']
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError('任务文件已存在，未覆盖')
    if os.name == 'nt':
        # No encoding declaration: COM accepts a UTF-16 BSTR, while files are UTF-8.
        text = ('<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
                '<Triggers><LogonTrigger><Repetition><Interval>PT30M</Interval>'
                '<StopAtDurationEnd>false</StopAtDurationEnd></Repetition><Enabled>true</Enabled>'
                '</LogonTrigger></Triggers><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
                '<StartWhenAvailable>true</StartWhenAvailable></Settings><Actions>'
                f'<Exec><Command>{escape(sys.executable)}</Command>'
                f'<Arguments>{escape(subprocess.list2cmdline(command[1:]))}</Arguments></Exec>'
                '</Actions></Task>')
        instructions = '在 Windows 任务计划程序中导入 XML；以当前用户运行，不要选择 SYSTEM。'
    elif sys.platform == 'darwin':
        import plistlib
        text = plistlib.dumps(dict(Label='local.ocusage.sync', ProgramArguments=command,
                                  StartInterval=1800, RunAtLoad=True)).decode('utf-8')
        instructions = '检查后放入 ~/Library/LaunchAgents，再使用 launchctl bootstrap 注册。'
    else:
        # A cron template is portable and does not require a user systemd session.
        import shlex
        text = '*/30 * * * * ' + shlex.join(command) + '\n'
        instructions = '检查后通过 crontab -e 添加此行。'
    with output.open('x', encoding='utf-8') as stream:
        stream.write(text)
    return dict(ok=True, path=str(output.resolve()), instructions=instructions,
                registered=False)


def run(args):
    home = core.DEFAULT_LEDGER.parent
    output = io.StringIO()
    result = {}
    code = 0
    try:
        with redirect_stdout(output), workspace_lock(home):
            config = configuration(home)
            if args.command == 'init':
                device = identity(config, home, lambda _: False)
                organize_state(home)
                import socket
                config['device'] = device
                config['device_name'] = args.device_name or config.get('device_name') or socket.gethostname()
                if len(config['device_name']) > 80 or any(ord(c) < 32 for c in config['device_name']):
                    raise ValueError('设备名无效')
                if args.db:
                    from .paths import local_path
                    db = local_path(args.db).resolve()
                    discover({'db': str(db)})
                    with core.source_connection(db) as source:
                        core.source_rows(source)
                    config['db'] = str(db)
                if args.exchange:
                    from .paths import local_path
                    from .sync import validate_exchange
                    exchange = validate_exchange(home, local_path(args.exchange))
                    exchange.mkdir(parents=True, exist_ok=True)
                    config['exchange'] = str(exchange)
                core.atomic_json(home/'menu-config.json', config)
                result = dict(ok=True, workspace=str(home), device=device, config=config)
            elif args.command == 'sync':
                if not config.get('device_name'):
                    raise ValueError('请先运行 ocusage init 或 ocu 完成设备设置')
                device = identity(config, home, lambda _: False)
                organize_state(home)
                result = refresh(config, device, home, open_report=not args.no_open)
                if not result['ok']:
                    code = 1
                log_path = state_path(home, 'sync.log')
                log_path.parent.mkdir(parents=True, exist_ok=True)
                if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                    os.replace(log_path, state_path(home, 'sync.previous.log'))
                with log_path.open('a', encoding='utf-8') as log:
                    log.write(json.dumps(result, ensure_ascii=False) + '\n')
            elif args.command == 'doctor':
                result = diagnose(home, config)
                code = 0 if result['ok'] else 1
            elif args.command == 'schedule':
                result = schedule(args.output)
            else:
                from .sync import status
                result = dict(ok=True, workspace=str(home), sync=status(home))
                path = state_path(home, 'last-sync.json')
                if path.exists():
                    result['last_run'] = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError, sqlite3.Error) as exc:
        result = dict(ok=False, error=str(exc), at=datetime.now(timezone.utc).isoformat())
        code = 3 if '其他 OCU 进程' in str(exc) else 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(output.getvalue(), end='')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if code:
        raise SystemExit(code)
    return result


def refresh(config, device, home, request_input=None, open_report=None):
    """Shared refresh workflow; optional callbacks keep terminal input in the menu."""
    from . import actions, dashboard, safety, sync
    from .interaction import state, changes
    ledger = core.DEFAULT_LEDGER
    config_path = home/'menu-config.json'
    safety.backup(home, 'refresh')
    before = state(ledger)
    errors = []
    legacy = Path.home()/'.local/share/opencode/reports/ocstats-ledger.db'
    if legacy.is_file() and not config.get('legacy_migrated'):
        count = dashboard.migrate_legacy(legacy, ledger, device, config['device_name'])
        config['legacy_migrated'] = True
        actions.notice(f'旧台账已合入 {count} 条，原文件保持不变。', 'ok')
    db, collected, local_sessions = None, False, []
    try:
        db = discover(config)
        if db is None and request_input:
            db = request_input('source', core.DEFAULT_DB)
        if db is None:
            raise ValueError('未找到本机数据库，请运行 ocusage init --db PATH')
        with core.source_connection(db) as source:
            source.execute('BEGIN')
            core.collect(source, ledger, device, config['device_name'])
            collected = True
            try:
                dashboard.refresh_metadata(source, ledger)
                local_sessions = dashboard.current_sessions(source)
            except (ValueError, OSError, sqlite3.Error) as exc:
                errors.append(f'collect: 会话信息更新失败：{exc}')
                actions.notice(f'用量已保存，但会话信息更新失败：{exc}', 'warn')
    except (ValueError, OSError, sqlite3.Error) as exc:
        errors.append(f'collect: {exc}')
        actions.notice(f'采集未完成：{exc}；仍展示已保存历史。', 'warn')
        if db is not None:
            actions.notice(f'本次源数据库：{db.resolve()}')
    if db is not None and db.is_file():
        config['db'] = str(db.resolve())
    core.atomic_json(config_path, config)
    local_after = state(ledger)
    collection_path = state_path(home, 'collection-status.json')
    previous = json.loads(collection_path.read_text(encoding='utf-8')) if collection_path.exists() else {}
    now = datetime.now(timezone.utc).isoformat()
    core.atomic_json(collection_path, dict(ok=collected, checked_at=now,
        last_success=now if collected else previous.get('last_success')))
    local_added, local_changed = changes(before, local_after)
    paths = list(inbox(home).glob('*.snapshot.json')) + list((home/'imports').glob('*.snapshot.json'))
    if config.get('exchange'):
        paths += [p for p in Path(config['exchange']).glob('*.snapshot.json') if p.name != f'{device}.snapshot.json']
    skipped = failures = metadata_updated = 0
    unique = {}
    for path in paths:
        unique.setdefault(path.resolve(), path)
    for saved in sorted(unique.values()):
        try:
            data = core.read_snapshot(saved)
            if sync.receive(home, data, saved, check_only=True):
                skipped += 1
                sync.consume(home, saved)
                continue
            aliases = config.setdefault('device_aliases', {})
            if data['version'] == 1 and data['device'] not in aliases:
                aliases[data['device']] = request_input('legacy', data) if request_input else data['device']
            merged = sync.accept(home, saved, data, ledger, aliases.get(data['device']) if data['version'] == 1 else None)
            metadata_updated += merged['metadata_updated']
            config.pop('processed_snapshots', None)
            core.atomic_json(config_path, config)
        except (ValueError, OSError, sqlite3.Error) as exc:
            failures += 1
            errors.append(f'import {saved.name}: {exc}')
            actions.notice(f'快照未合入：{saved.name} · {exc}；请保留源文件后重试。', 'warn')
    added, changed = changes(local_after, state(ledger))
    if config.get('exchange'):
        try:
            sync.read_heartbeats(home, config['exchange'])
        except (OSError, ValueError) as exc:
            errors.append(f'import: 设备状态读取失败：{exc}')
            actions.notice(f'设备状态读取失败：{exc}；已合并用量保留。', 'warn')
    config['pending_snapshots'] = config['last_failures'] = failures
    core.atomic_json(config_path, config)
    if not ledger.is_file():
        actions.notice('尚无可用台账，本次不生成报告或导出。请修复源数据库读取问题后重新刷新。', 'warn')
        result = dict(ok=False, errors=errors, records=0, at=datetime.now(timezone.utc).isoformat())
        core.atomic_json(state_path(home, 'last-sync.json'), result)
        return result
    if config.get('exchange'):
        try:
            sync.publish(home, ledger, device, config)
            actions.notice(f"已发布{'全部历史' if config.get('relay_history') else '本机来源'}快照 · {'含标题/路径，请仅用于可信设备' if config.get('sync_metadata') else '仅用量'}；云盘传输待同步工具确认。", 'ok')
        except (ValueError, OSError, sqlite3.Error) as exc:
            errors.append(f'export: {exc}')
            actions.notice(f'发布失败：{exc}；继续生成本地报告。', 'warn')
    summary = [f'本机新增 {local_added} 条' if local_added else '本机无新增']
    for label, value in (('本机用量更新', local_changed), ('导入新增', added), ('导入用量更新', changed)):
        if value:
            summary.append(f'{label} {value} 条')
    if metadata_updated:
        summary.append(f'会话信息补齐/更新 {metadata_updated} 个')
    if failures:
        summary.append(f'{failures} 份导入失败')
    actions.notice(' · '.join(summary), 'warn' if errors else 'ok')
    if actions.show_report(device, local_sessions, open_report=open_report) is None:
        errors.append('report: 报告生成失败')
    result = dict(ok=not errors, errors=errors, records=len(state(ledger)),
        imported=dict(added=added, updated=changed, metadata_updated=metadata_updated, skipped=skipped, failed=failures),
        at=datetime.now(timezone.utc).isoformat())
    core.atomic_json(state_path(home, 'last-sync.json'), result)
    return result
