from code_harness.application.tools._timing import timed
from code_harness.domain.models.index_report import DoctorReport
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.diagnostic_provider import DiagnosticProvider


class DoctorTool:
    def __init__(self, provider: DiagnosticProvider) -> None:
        self._provider = provider

    def execute(self, *, deep: bool = False) -> ToolResult[DoctorReport]:
        report, elapsed_ms = timed(lambda: self._provider.run(deep=deep))
        warnings = tuple(
            check.message for check in report.checks if check.status.value == "warning"
        )
        return ToolResult(report, elapsed_ms, warnings=warnings)
