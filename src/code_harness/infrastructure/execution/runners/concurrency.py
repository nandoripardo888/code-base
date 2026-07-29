from __future__ import annotations

import os
import threading
from importlib import import_module
from pathlib import Path
from typing import BinaryIO, Protocol, cast

_PROCESS_LOCK = threading.Lock()
_PROCESS_HELD: set[tuple[str, int]] = set()


class _FcntlModule(Protocol):
    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def lockf(
        self,
        fd: int,
        operation: int,
        length: int,
        start: int,
        whence: int,
    ) -> None: ...


class ProjectExecutionLease:
    def __init__(self, path: Path, slot_index: int, handle: BinaryIO) -> None:
        self.path = path
        self.slot_index = slot_index
        self._handle: BinaryIO | None = handle

    def release(self) -> None:
        with _PROCESS_LOCK:
            handle = self._handle
            if handle is None:
                return
            try:
                _unlock(handle, self.slot_index)
            finally:
                handle.close()
                self._handle = None
                _PROCESS_HELD.discard((str(self.path), self.slot_index))

    def __enter__(self) -> ProjectExecutionLease:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class ProjectExecutionLimiter:
    """Crash-safe, non-blocking concurrency slots scoped to one project."""

    def __init__(self, execution_home: Path, *, max_concurrent: int) -> None:
        self.max_concurrent = max_concurrent
        self.path = (execution_home / ".execution-concurrency.lock").resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            if self.path.stat().st_size < max_concurrent:
                handle.truncate(max_concurrent)

    def try_acquire(self) -> ProjectExecutionLease | None:
        for slot_index in range(self.max_concurrent):
            lease = self.try_acquire_slot(slot_index)
            if lease is not None:
                return lease
        return None

    def try_acquire_slot(self, slot_index: int) -> ProjectExecutionLease | None:
        if not 0 <= slot_index < self.max_concurrent:
            return None
        key = (str(self.path), slot_index)
        with _PROCESS_LOCK:
            if key in _PROCESS_HELD:
                return None
            handle = self.path.open("r+b", buffering=0)
            try:
                _lock_nonblocking(handle, slot_index)
            except OSError:
                handle.close()
                return None
            _PROCESS_HELD.add(key)
            return ProjectExecutionLease(self.path, slot_index, handle)


def _lock_nonblocking(handle: BinaryIO, slot_index: int) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(slot_index)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl = cast(_FcntlModule, import_module("fcntl"))
    fcntl.lockf(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1, slot_index, os.SEEK_SET)


def _unlock(handle: BinaryIO, slot_index: int) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(slot_index)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl = cast(_FcntlModule, import_module("fcntl"))
    fcntl.lockf(handle.fileno(), fcntl.LOCK_UN, 1, slot_index, os.SEEK_SET)
