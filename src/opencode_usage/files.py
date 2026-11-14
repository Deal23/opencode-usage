"""Replace generated files only after a complete, flushed write."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


@contextmanager
def atomic_output(path, encoding='utf-8', newline=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline=newline) as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, path)
        except PermissionError as exc:
            raise PermissionError(f'无法替换文件：{path}；请关闭占用它的 Excel 或其他程序，并检查目录写入权限后重试。原文件未替换。') from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
