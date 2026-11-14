"""Small reusable presentation workflows, independent of the menu loop."""
from datetime import datetime
from pathlib import Path
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import webbrowser

from . import core
from .interaction import state
from .terminal import notice
from .workspace import state_path


def export_filename(device_name):
    """Keep the readable device name in a portable, timestamped filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', device_name).strip(' .')[:80] or 'device'
    return f"usage-{name}-{datetime.now():%Y%m%d-%H%M%S}.snapshot.json"


def prune_exports(home, latest):
    """Retain the successful replacement, never prune external or unknown files."""
    folder, latest = Path(home)/'exports', Path(latest)
    if folder.is_symlink() or latest.is_symlink() or latest.parent.resolve() != folder.resolve():
        return
    core.read_snapshot(latest)
    for path in folder.iterdir():
        if path.resolve() == latest.resolve() or path.is_symlink() or not path.is_file():
            continue
        if not re.fullmatch(r'usage-(?:(?:.+-)?\d{8}-\d{6}|\d{8}T\d{12}Z)\.snapshot\.json', path.name):
            continue
        try:
            core.read_snapshot(path)
            path.unlink()
        except (ValueError, OSError) as exc:
            notice(f'旧导出未清理：{path.name} · {exc}', 'warn')


def export_summary(path, complete=True):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    rows, sessions = data['records'], data.get('sessions', [])
    sids = {r['session_id'] for r in rows}
    titled = sum(bool(s.get('title')) for s in sessions)
    projects = sum(bool(s.get('project')) for s in sessions)
    attributed = sum(bool(data.get('sources', {}).get(r['message_id'])) for r in rows)
    mode = '完整合并快照（含标题/路径隐私）' if complete else '仅用量快照（不能补齐标题/项目）'
    return (f'{mode} · {len(rows):,} 条消息 · 来源 {attributed}/{len(rows)} 条 · '
            f'标题 {titled}/{len(sids)} 会话 · 项目 {projects}/{len(sids)} 会话；仅包含台账已有信息，不含正文或密钥。')


def choose_paths(directory=False):
    """Optional native picker; terminal-only installations need no Tk dependency."""
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        if directory:
            value = filedialog.askdirectory(title='选择目录')
            return [Path(value)] if value else []
        return [Path(p) for p in filedialog.askopenfilenames(title='选择快照，可多选',
            filetypes=[('OCU 快照', '*.snapshot.json'), ('JSON', '*.json')])]
    except (ImportError, RuntimeError, OSError) as exc:
        notice(f'文件选择不可用，请输入路径：{exc}', 'warn')
        return []
    except Exception as exc:
        # TclError is unavailable to import when Tk itself is not installed.
        if type(exc).__name__ != 'TclError':
            raise
        notice('当前环境没有图形文件选择器，请输入路径。', 'warn')
        return []
    finally:
        if root is not None:
            root.destroy()


def preview(source, ledger):
    data = source if isinstance(source, dict) else core.read_snapshot(source)
    rows = data['records']
    existing = state(ledger)
    counts = dict(added=0, updated=0, same=0, conflicts=0)
    for row in rows:
        counts[core.compare(row, existing.get(row['message_id']))] += 1
    from .reporting import settings
    tz = settings(Path(ledger).parent)['tz']
    dates = [datetime.fromtimestamp(r['created_ms']/1000, tz).strftime('%Y-%m-%d') for r in rows]
    return dict(device=data.get('device_name') or data['device'], count=len(rows),
                start=min(dates, default='—'), end=max(dates, default='—'),
                metadata=bool(data.get('sessions')), **counts)


def open_directory(path):
    return open_path(path, directory=True)


def open_path(path, directory=False):
    """Use native Windows associations and keep launch errors non-fatal."""
    path = Path(path).resolve()
    try:
        if not path.exists():
            raise FileNotFoundError(str(path))
        if os.name == 'nt':
            os.startfile(str(path))
            return True
        if not directory:
            headless = sys.platform.startswith('linux') and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
            if not headless and webbrowser.open(path.as_uri()):
                return True
        elif sys.platform == 'darwin':
            subprocess.run(['open', str(path)], check=True)
            return True
        elif os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
            opener = shutil.which('xdg-open')
            if opener:
                subprocess.run([opener, str(path)], check=True)
                return True
    except (OSError, subprocess.SubprocessError, webbrowser.Error) as exc:
        notice(f'无法打开{"目录" if directory else "报告"}：{exc}', 'warn')
    notice(f'{"目录" if directory else "HTML"}：{path}（请手动打开）', 'warn')
    return False


def view_report(home):
    home = Path(home)
    md, page = home/'reports/report.md', home/'reports/report.html'
    headless = sys.platform.startswith('linux') and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    if headless:
        if not md.exists():
            notice('尚无文字报告，请先更新统计。', 'warn')
            return
        less = shutil.which('less')
        if less and sys.stdin.isatty():
            subprocess.run([less, '--', str(md)], check=True)
        else:
            notice(f'Markdown：{md}')
    elif page.exists():
        open_path(page)
    else:
        notice('尚无报告，请先更新统计。', 'warn')


def summary(home):
    path = state_path(home, 'report-status.json')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        from .sync import status
        devices = status(home)['devices']
        stale = sum(bool(d.get('stale')) for d in devices.values())
        text = f"报告 {data['at']} · {data['records']:,} 条 · {data.get('tokens', 0)/1e6:.1f}M token"
        warnings = []
        if data.get('failures'):
            warnings.append(f"{data['failures']} 份导入失败")
        if stale:
            warnings.append(f'{stale} 台超过 24 小时未检查/无新快照')
        return text + ('\n注意：' + ' · '.join(warnings) if warnings else '')
    except (OSError, ValueError, KeyError, TypeError):
        return '尚无报告 · 选 1 生成第一份报告'


def show_report(device, local_sessions=None, open_report=None):
    try:
        return generate_report(device, local_sessions, open_report=open_report)
    except (ValueError, OSError, sqlite3.Error) as exc:
        notice(f'报告生成未完成：{exc}；已保存的台账未回退，可稍后重试。', 'error')


def generate_report(device, local_sessions=None, open_report=None):
    from . import dashboard
    from .interaction import configuration
    from .paths import local_path
    home = core.DEFAULT_LEDGER.parent
    report = home/'reports/report.html'
    settings = configuration(home)
    db = local_path(settings.get('db', core.DEFAULT_DB))
    try:
        payload = dashboard.render(core.DEFAULT_LEDGER, report, db=db, device_name=settings.get('device_name', '本机'), local_sessions=local_sessions)
    except (ValueError, OSError, sqlite3.Error) as exc:
        notice(f'本机会话读取失败：{exc}；累计历史仍保留。', 'warn')
        payload = dashboard.render(core.DEFAULT_LEDGER, report)
    count = payload['completeness']['messages']
    notice(f'报告已生成 · {count:,} 条累计记录', 'ok')
    notice(f'报告目录：{report.parent}（HTML / Markdown）')
    core.atomic_json(state_path(home, 'report-status.json'), dict(at=datetime.now().strftime('%m/%d %H:%M'), records=count,
        devices=len(payload.get('devices', [])), tokens=sum(r['tot'] for r in payload['rows']), failures=settings.get('last_failures', 0)))
    obsolete = report.parent/'total.snapshot.json'
    if obsolete.is_file() and not obsolete.is_symlink():
        try:
            core.read_snapshot(obsolete)
        except (ValueError, OSError):
            notice('旧报告快照无法确认格式，已保留 total.snapshot.json。', 'warn')
        else:
            try:
                obsolete.unlink()
            except OSError as exc:
                notice(f'最新报告已保存，旧报告快照暂未清理：{exc}', 'warn')
    should_open = settings.get('open_browser', True) if open_report is None else open_report
    if should_open:
        open_path(report)
    return payload


def export_csv(home):
    from .dashboard import render
    folder = Path(home)/'reports'
    render(Path(home)/'ledger.db', folder/'report.html', local_sessions=[], csv_only=True)
    notice(f'CSV 已导出：{folder}（total.csv / reference-costs.csv，按需更新）', 'ok')
