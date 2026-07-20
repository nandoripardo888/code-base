from code_harness.application.dto.requests import IndexProjectRequest
from code_harness.application.indexing.index_coordinator import IndexCoordinator
from code_harness.application.tools._timing import timed
from code_harness.domain.models.index_report import IndexReport
from code_harness.domain.models.tool_result import ToolResult


class IndexProjectTool:
    def __init__(self, coordinator: IndexCoordinator) -> None:
        self._coordinator = coordinator

    def execute(self, request: IndexProjectRequest) -> ToolResult[IndexReport]:
        report, elapsed_ms = timed(lambda: self._coordinator.index(request.mode))
        return ToolResult(
            report,
            elapsed_ms,
            warnings=report.warnings,
            index_state=report.state.value,
        )
