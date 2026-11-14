"""Local identity, path discovery, and change summaries."""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import uuid

from . import core
from .paths import local_path


def configuration(home):
    """Read and normalize the shared menu/automation configuration."""
    path = home/'menu-config.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError('菜单配置必须是 JSON 对象')
    for key in ('db', 'exchange'):
        if data.get(key):
            data[key] = str(local_path(data[key]))
    return data


def identity(config, home, confirm):
    path = Path.home() / '.config/ocusage/identity.json'
    if path.exists():
        local = json.loads(path.read_text(encoding='utf-8'))['id']
    else:
        if config.get('device') and not confirm('首次绑定：确认此工作目录属于当前机器？[y/N]：'):
            raise ValueError('未绑定。请使用独立工作目录，不要接管其他设备台账。')
        local = config.get('device') or str(uuid.uuid4())
        core.atomic_json(path, {'id': local})
    owner = home / 'workspace-owner.json'
    if owner.exists() and json.loads(owner.read_text(encoding='utf-8'))['id'] != local:
        raise ValueError('此工作目录属于其他设备，已阻止写入。请在 ~/.config/ocusage/workspace.json 指向本机独立目录；其他设备数据通过快照导入。')
    if config.get('device') and config['device'] != local:
        raise ValueError('工作目录设备身份与本机不符，已阻止写入。请使用本机独立工作目录。')
    core.atomic_json(owner, {'id': local})
    return local


def discover(config):
    if config.get('db'):
        path = local_path(config['db'])
        if not path.is_file():
            raise ValueError(f'指定的数据库不存在：{path}；请在设置中修改数据库路径，不会自动切换到其他库。')
        return path
    candidates = []
    candidates += [Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'opencode/opencode.db', core.DEFAULT_DB]
    for key in ('LOCALAPPDATA', 'APPDATA'):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key])/'opencode/opencode.db')
    for path in candidates:
        if path.is_file():
            try:
                with closing(core.readonly(path)) as c:
                    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables & {'message', 'session_message'}:
                    return path
            except sqlite3.Error:
                pass
    return None


def state(ledger):
    if not ledger.exists():
        return {}
    with closing(core.readonly(ledger)) as c:
        return {r[0]: r[1:] for r in c.execute('SELECT '+','.join(core.FIELDS)+' FROM usage')}


def changes(before, after):
    return len(after.keys()-before.keys()), sum(before[k] != after[k] for k in before.keys() & after.keys())
