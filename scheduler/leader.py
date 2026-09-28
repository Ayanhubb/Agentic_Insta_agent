"""Keep a single in-process scheduler loop on one host.

Each FastAPI worker runs the application lifespan. A unique database row does
not stop two workers from both ticking. The worker that acquires this exclusive
file lock starts the loop and holds the lock until shutdown. Other workers in
the same container skip the loop.

This lock is local to one machine. A deployment with more than one host still
needs a single scheduler process. That multi-host lock is not implemented.
"""

from __future__ import annotations

import os
from pathlib import Path


class SchedulerLeader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
