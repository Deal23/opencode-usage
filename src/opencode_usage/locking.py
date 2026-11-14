"""Process-scoped advisory workspace lock, released by the OS after a crash."""
from contextlib import contextmanager
from pathlib import Path
import os


@contextmanager
def workspace_lock(home):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    with (home / '.ocusage.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError(f'工作目录已被其他 OCU 进程使用，请先退出另一个窗口：{home}') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
