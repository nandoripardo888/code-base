from typing import Protocol

from code_harness.domain.models.capability import CapabilityStatus
from code_harness.domain.models.index_report import IndexStatus


class CapabilityReporter(Protocol):
    def report(self, status: IndexStatus) -> tuple[CapabilityStatus, ...]: ...

    def invalidate_semantic_health(self) -> None: ...
