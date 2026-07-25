from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class IndexProgressPhase(StrEnum):
    INITIALIZING = "initializing"
    DISCOVERING = "discovering"
    ANALYZING = "analyzing"
    EMBEDDING = "embedding"
    COMMITTING = "committing"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class IndexProgressEvent:
    phase: IndexProgressPhase
    current: int = 0
    total: int = 0
    path: str | None = None
    message: str | None = None

    @property
    def percent(self) -> int | None:
        if self.total <= 0:
            return None
        return min(100, int((100 * self.current) / self.total))


IndexProgressCallback = Callable[[IndexProgressEvent], None]
