"""工作区 Markdown 的进程锁与原子替换。"""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def record_lock(path: Path):
    """串行化线程和进程之间的读取、修改与写入。"""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    handle = lock_path.open("r+", encoding="utf-8")
    try:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - supported deployment is Linux
            yield
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
