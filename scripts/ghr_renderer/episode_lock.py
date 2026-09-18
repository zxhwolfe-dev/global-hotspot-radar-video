"""Fail-fast single-writer lock for the renderer CLI (POSIX/WSL and Windows).

The lock file is retained: unlinking it could let waiters lock different inodes.
This coordinates this CLI on one host, not distributed storage or orphan workers.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator


@contextmanager
def episode_lock(root: Path) -> Iterator[None]:
    path = root.resolve() / ".ghr-render.lock"
    # Reject existing symlinks: the lock is project-local, never an arbitrary file.
    if path.is_symlink():
        raise ValueError("episode render lock must not be a symlink")
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("episode is already being rendered; no work was started") from exc
            unlock = lambda: (handle.seek(0), msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1))
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("episode is already being rendered; no work was started") from exc
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            yield
        finally:
            unlock()
