from datetime import UTC, datetime

from code_harness.application.dto.execution_requests import TerminateExecutionRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import ExecutionState
from code_harness.domain.models.execution import ExecutionResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.command_policy import SensitiveValueRedactor
from code_harness.domain.protocols.execution_runtime import ExecutionRegistry
from code_harness.domain.protocols.execution_store import ExecutionStore

_TERMINAL_STATES = {
    ExecutionState.BLOCKED,
    ExecutionState.COMPLETED,
    ExecutionState.FAILED,
    ExecutionState.TIMED_OUT,
    ExecutionState.CANCELLED,
}


class TerminateExecutionTool:
    def __init__(
        self,
        *,
        registry: ExecutionRegistry,
        store: ExecutionStore,
        redactor: SensitiveValueRedactor,
    ) -> None:
        self._registry = registry
        self._store = store
        self._redactor = redactor

    def execute(self, request: TerminateExecutionRequest) -> ToolResult[ExecutionResult]:
        def terminate() -> ExecutionResult:
            current = self._registry.get(request.execution_id)
            if current.state in _TERMINAL_STATES:
                return current
            reason = self._redactor.redact(request.reason)
            audit_error: Exception | None = None
            try:
                self._store.record_cancellation_requested(
                    request.execution_id,
                    requested_at=datetime.now(UTC).isoformat(),
                    reason=reason,
                )
            except Exception as error:
                audit_error = error
            result = self._registry.terminate(
                request.execution_id,
                reason=reason,
            )
            if audit_error is not None:
                raise audit_error
            return result

        result, elapsed_ms = timed(terminate)
        return ToolResult(result, elapsed_ms)
