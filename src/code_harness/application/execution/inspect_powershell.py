from code_harness.application.dto.execution_requests import InspectPowerShellRequest
from code_harness.application.tools._timing import timed
from code_harness.domain.errors import ExecutionElevatedSessionError, InvalidExecutionRequestError
from code_harness.domain.models.execution import (
    CommandInspection,
    ExecutionRuntimeConfig,
    NormalizedPowerShellCommand,
)
from code_harness.domain.models.tool_result import ToolResult, normalize_warnings
from code_harness.domain.protocols.command_policy import CommandPolicy, WorkspacePathResolver


class InspectPowerShellTool:
    def __init__(
        self,
        *,
        paths: WorkspacePathResolver,
        policy: CommandPolicy,
        config: ExecutionRuntimeConfig,
    ) -> None:
        self._paths = paths
        self._policy = policy
        self._config = config

    def execute(self, request: InspectPowerShellRequest) -> ToolResult[CommandInspection]:
        def inspect() -> CommandInspection:
            if self._config.elevated_session and not self._config.allow_elevated:
                raise ExecutionElevatedSessionError()
            absolute_cwd, _relative = self._paths.resolve_within_root(
                request.cwd,
                expected_kind="directory",
                must_exist=True,
            )
            timeout = (
                request.timeout_seconds
                if request.timeout_seconds is not None
                else self._config.default_timeout_seconds
            )
            if timeout > self._config.max_timeout_seconds:
                raise InvalidExecutionRequestError(
                    "timeout_seconds exceeds execution_max_timeout_seconds",
                    timeout_seconds=timeout,
                    max_timeout_seconds=self._config.max_timeout_seconds,
                )
            max_output = (
                request.max_output_bytes
                if request.max_output_bytes is not None
                else self._config.max_output_bytes
            )
            command = NormalizedPowerShellCommand(
                script=request.script,
                cwd=absolute_cwd,
                timeout_seconds=timeout,
                max_output_bytes=max_output,
                requested_capabilities=request.requested_capabilities,
                reason=request.reason,
            )
            return self._policy.inspect_powershell(command)

        inspection, elapsed_ms = timed(inspect)
        return ToolResult(
            inspection,
            elapsed_ms,
            warnings=normalize_warnings(
                inspection.warnings,
                code="execution_inspection_warning",
                capability="execution",
            ),
        )
