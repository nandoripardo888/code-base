from code_harness.application.dto.execution_requests import GetExecutionRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.models.execution import ExecutionResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.execution_runtime import ExecutionRegistry


class GetExecutionTool:
    def __init__(self, registry: ExecutionRegistry) -> None:
        self._registry = registry

    def execute(self, request: GetExecutionRequest) -> ToolResult[ExecutionResult]:
        result, elapsed_ms = timed(
            lambda: self._registry.get(
                request.execution_id,
                include_output=request.include_output,
            )
        )
        return ToolResult(result, elapsed_ms)
