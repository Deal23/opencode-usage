"""Select and migrate a single-device workspace; preserve the source as backup."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from .paths import local_path


STATE_FILES = ('collection-status.json', 'last-sync.json', 'report-status.json',
               'sync-state.json', 'update-check.json', 'pending-update.json',
               'sync.log', 'sync.previous.log', 'diagnostics.json')


def state_path(home, name):
    """Read legacy locations until the next locked startup migrates them."""
    if name not in STATE_FILES:
        raise ValueError(f'未知内部状态文件：{name}')
    home = Path(home)
    folder = home/'.state'
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise ValueError('内部状态目录 .state 不可用')
    legacy = home/name
    target = folder/name
    if legacy.is_symlink() or target.is_symlink():
        raise ValueError(f'内部状态文件不能是符号链接：{name}')
    if legacy.exists() and target.exists():
        raise ValueError(f'状态文件位置冲突，请保留两份并检查：{name}')
    return legacy if legacy.exists() else target


def organize_state(home):
    """Move only known internal files while the caller holds the workspace lock."""
    home = Path(home)
    folder = home/'.state'
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise ValueError('内部状态目录 .state 不可用；未移动文件')
    pending = [home/name for name in STATE_FILES if (home/name).exists() or (home/name).is_symlink()]
    for source in pending:
        if source.is_symlink() or not source.is_file() or (folder/source.name).exists() or (folder/source.name).is_symlink():
            raise ValueError(f'状态文件迁移冲突，未覆盖：{source.name}')
    if pending:
        folder.mkdir(exist_ok=True)
        for source in pending:
            source.rename(folder/source.name)
    organize_inbox(home)


def inbox(home):
    """Private retry queue, created only when needed."""
    folder = Path(home)/'.state'
    target = folder/'inbox'
    if folder.is_symlink() or target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError('导入队列目录不可用')
    return target


def organize_inbox(home):
    legacy = Path(home)/'imports'
    target = inbox(home)
    if not legacy.exists() and not legacy.is_symlink():
        return
    if legacy.is_symlink() or not legacy.is_dir():
        raise ValueError('旧导入目录不可用，未迁移')
    paths = list(legacy.glob('*.snapshot.json'))
    for path in paths:
        if path.is_symlink() or not path.is_file() or (target/path.name).exists() or (target/path.name).is_symlink():
            raise ValueError(f'导入队列迁移冲突，未覆盖：{path.name}')
    if paths:
        target.mkdir(parents=True, exist_ok=True)
        for path in paths:
            path.rename(target/path.name)
    if not any(legacy.iterdir()):
        try:
            legacy.rmdir()
        except OSError:
            from .terminal import notice
            notice('旧 imports 已为空，但系统暂不允许删除；已保留，不影响使用。', 'warn')


def pointer():
    return Path.home() / ".config/ocusage/workspace.json"


def current():
    p = pointer()
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        path = local_path(data["workspace"])
        if not path.is_absolute() or not path.is_dir():
            raise ValueError(f"工作目录不可用，请检查云盘挂载或 {p}")
        return path
    return Path.home() / ".local/share/ocusage"


def migrate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).expanduser().resolve()
    if not source.is_dir() or source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("目标必须是源目录之外的新目录")
    if destination.exists():
        raise ValueError("目标目录已存在，请选择一个尚不存在的目录，避免覆盖数据")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ocu-migrate-", dir=destination.parent))
    try:
        # Do not copy SQLite live sidecars or traverse user symlinks.
        for p in source.rglob("*"):
            if p.is_symlink():
                raise ValueError(f"工作目录含符号链接，停止迁移：{p}")
        shutil.copytree(source, stage, dirs_exist_ok=True, ignore=shutil.ignore_patterns("ledger.db", "ledger.db-wal", "ledger.db-shm", "ledger.db-journal", ".ocusage.lock"))
        for p in source.rglob("*"):
            if p.is_file() and p.name not in {"ledger.db", "ledger.db-wal", "ledger.db-shm", "ledger.db-journal", ".ocusage.lock"}:
                copied = stage / p.relative_to(source)
                if hashlib.sha256(p.read_bytes()).digest() != hashlib.sha256(copied.read_bytes()).digest():
                    raise ValueError(f"文件校验失败：{p}")
        ledger = source / "ledger.db"
        if ledger.exists():
            with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(stage / "ledger.db")) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("迁移台账完整性检查失败")
        stage.rename(destination)
        config = pointer()
        config.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".workspace-", dir=config.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"workspace": str(destination)}, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, config)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return destination
