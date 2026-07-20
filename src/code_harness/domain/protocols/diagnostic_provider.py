from typing import Protocol

from code_harness.domain.models.index_report import DoctorReport


class DiagnosticProvider(Protocol):
    def run(self, *, deep: bool = False) -> DoctorReport: ...
