"""Read Windows WAL databases from WSL through a consistent SQLite backup."""
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile


class SnapshotConnection(sqlite3.Connection):
    def close(self):
        try:
            super().close()
        finally:
            snapshot = getattr(self, '_snapshot', None)
            if snapshot is not None:
                snapshot.cleanup()
                self._snapshot = None


def windows_snapshot(path):
    """Use the Windows SQLite VFS; never bypass WAL or alter the source."""
    python = shutil.which('python.exe') or shutil.which('py.exe')
    if not python:
        raise ValueError('WSL 无法打开 Windows 数据库的 WAL 共享内存；需要可从 WSL 调用的 Windows Python（python.exe 或 py.exe）。')
    command = [python] + (['-3'] if Path(python).name.lower() == 'py.exe' else [])

    def run(args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError('Windows SQLite 一致性备份失败：' + result.stderr.strip())
        return result.stdout.strip()

    windows_temp = run(command + ['-c', 'import tempfile; print(tempfile.gettempdir())'])
    mounted_temp = run(['wslpath', '-u', windows_temp])
    snapshot = tempfile.TemporaryDirectory(prefix='ocusage-sqlite-', dir=mounted_temp)
    try:
        target = Path(snapshot.name) / 'snapshot.db'
        source_windows = run(['wslpath', '-w', str(path)])
        target_windows = run(['wslpath', '-w', str(target)])
        script = (
            'import sqlite3, sys; from pathlib import Path; '
            'src=sqlite3.connect(Path(sys.argv[1]).as_uri()+"?mode=ro", uri=True); '
            'dst=sqlite3.connect(sys.argv[2]); '
            'src.backup(dst); dst.execute("PRAGMA journal_mode=DELETE").fetchone(); '
            'dst.close(); src.close()'
        )
        run(command + ['-c', script, source_windows, target_windows])
        connection = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True,
                                     factory=SnapshotConnection)
        connection._snapshot = snapshot
        return connection
    except BaseException:
        snapshot.cleanup()
        raise


def needs_windows_snapshot(path, error):
    return (os.name == 'posix'
            and 'microsoft' in os.uname().release.lower()
            and str(path).startswith('/mnt/')
            and getattr(error, 'sqlite_errorcode', None) in (4618, 4874, 5130, 5386))
