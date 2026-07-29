from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import TracebackType

from code_harness.domain.errors import ChangeSessionLockHeldError


class FileSessionLock:
    def __init__(self, key: str, path: Path) -> None:
        self._key = key
        self._path = path
        self._handle: object | None = None

    @property
    def key(self) -> str:
        return self._key

    def __enter__(self) -> FileSessionLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"\0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise ChangeSessionLockHeldError(self._key) from error
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


class FileSessionLockManager:
    def __init__(self, locks_home: Path | str) -> None:
        self._home = Path(locks_home)

    def acquire(self, key: str) -> FileSessionLock:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        path = self._home / f"{digest}.lock"
        return FileSessionLock(key, path)

    @staticmethod
    def git_lock_key(repository_root: str, target_branch: str) -> str:
        identity = os.path.normcase(str(Path(repository_root).resolve(strict=False)))
        return f"git:{identity}:{target_branch}"
