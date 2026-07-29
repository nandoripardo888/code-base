from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Protocol


class IsolatedWorktree(Protocol):
    def __enter__(self) -> Path: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class IsolatedWorktreeFactory(Protocol):
    def create(
        self,
        *,
        change_set_id: str,
        base_ref: str | None,
        unified_diff: str,
    ) -> IsolatedWorktree: ...
