from __future__ import annotations

from types import TracebackType

from code_harness.domain.protocols.change_isolation import SessionLock, SessionLockManager


class OrderedSessionLocks:
    """Acquire multiple session locks in deterministic order; release on failure."""

    def __init__(self, manager: SessionLockManager, keys: tuple[str, ...]) -> None:
        self._manager = manager
        self._keys = tuple(sorted(set(keys)))
        self._held: list[SessionLock] = []

    def __enter__(self) -> OrderedSessionLocks:
        try:
            for key in self._keys:
                lock = self._manager.acquire(key)
                lock.__enter__()
                self._held.append(lock)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        while self._held:
            lock = self._held.pop()
            lock.__exit__(exc_type, exc, tb)
