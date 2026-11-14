"""Bounded, consistent ledger backups and explicit restore."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3

from . import core


def backups(home):
    return sorted((Path(home) / 'backups').glob('auto-*.db'), reverse=True)


def backup(home, reason, keep=None):
    home = Path(home)
    ledger = home / 'ledger.db'
    if not ledger.exists():
        return None
    if keep is None:
        config = home / 'menu-config.json'
        settings = json.loads(config.read_text(encoding='utf-8')) if config.exists() else {}
        keep = settings.get('backup_keep', 5)
    if type(keep) is not int or not 1 <= keep <= 50:
        raise ValueError('备份保留份数必须为 1–50')
    dest = home / 'backups' / f"auto-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{reason}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with closing(core.readonly(ledger)) as source, closing(sqlite3.connect(dest)) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('备份完整性检查失败，已停止写入')
    config = home / 'menu-config.json'
    if config.exists():
        core.atomic_json(dest.with_suffix('.json'), json.loads(config.read_text(encoding='utf-8')))
    for old in backups(home)[max(1, keep):]:
        old.unlink()
        old.with_suffix('.json').unlink(missing_ok=True)
    return dest


def restore(home, selected):
    """Restore ledger only; preserve current device identity, paths and preferences."""
    home, selected = Path(home), Path(selected)
    if selected.resolve() not in [p.resolve() for p in backups(home)]:
        raise ValueError('只能恢复当前工作目录列出的自动备份')
    # In-memory copy keeps selected content safe even if retention rotates its file.
    with closing(sqlite3.connect(':memory:')) as saved:
        with closing(core.readonly(selected)) as source:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('备份损坏，未恢复')
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'usage' not in tables:
                raise ValueError('备份不是 OCU 台账')
            source.backup(saved)
        safety = backup(home, 'before-restore')
        with closing(sqlite3.connect(home / 'ledger.db')) as target:
            saved.backup(target)
    return safety
