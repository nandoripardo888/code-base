"""Opt-in MCP adapter for supervised command execution."""

from __future__ import annotations

import logging
import threading
import weakref
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import Context, FastMCP

from code_harness.application.dto.execution_requests import (
    GetExecutionRequest,
    InspectPowerShellRequest,
    InspectProcessRequest,
    RunPowerShellRequest,
    RunProcessRequest,
    TerminateExecutionRequest,
)
from code_harness.bootstrap.container import ApplicationContainer
from code_harness.bootstrap.execution import ExecutionContainer
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ApprovalChannel, ExecutionCapability
from code_harness.domain.errors import (
    CodeHarnessError,
    InternalToolError,
    InvalidExecutionRequestError,
)
from code_harness.domain.models.execution import CommandInspection
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.mcp.mcp_elicitation_channel import McpElicitationDecisionChannel
from code_harness.interfaces.serialization import serialize_error, serialize_tool_result

_LOGGER = logging.getLogger(__name__)
_SESSION_IDS: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
_SESSION_IDS_GUARD = threading.Lock()


def _execution(container: ApplicationContainer) -> ExecutionContainer:
    execution = container.execution
    if execution is None:
        raise InvalidExecutionRequestError("Execution tools are unavailable.")
    return execution


def _execute(operation: Callable[[], ToolResult[Any]], tool: str) -> dict[str, Any]:
    try:
        return serialize_tool_result(operation())
    except CodeHarnessError as error:
        return serialize_error(error)
    except ValueError as error:
        return serialize_error(InvalidExecutionRequestError(str(error)))
    except Exception:
        error_id = uuid4().hex
        _LOGGER.exception("Unexpected %s failure (error_id=%s).", tool, error_id)
        return serialize_error(InternalToolError(tool, error_id))


def _can_elicit(
    context: Context[Any, Any, Any],
    settings: Settings,
) -> bool:
    if settings.mcp_execution_approval_channel is not ApprovalChannel.MCP_ELICITATION:
        return False
    channel = McpElicitationDecisionChannel(context)
    return channel.supports_client()


def _session_id(context: Context[Any, Any, Any]) -> str:
    session = context.session
    with _SESSION_IDS_GUARD:
        value = _SESSION_IDS.get(session)
        if value is None:
            value = uuid4().hex
            _SESSION_IDS[session] = value
        return value


def _select_channel(
    *,
    context: Context[Any, Any, Any],
    container: ApplicationContainer,
    settings: Settings,
) -> tuple[Any, str | None]:
    execution = _execution(container)
    match settings.mcp_execution_approval_channel:
        case ApprovalChannel.MCP_ELICITATION if _can_elicit(context, settings):
            return McpElicitationDecisionChannel(context), _session_id(context)
        case ApprovalChannel.HOST_LOOPBACK if execution.approval_channel is not None:
            return execution.approval_channel, settings.service_instance_id
        case _:
            return None, None


async def _run_with_optional_elicitation(
    *,
    context: Context[Any, Any, Any],
    container: ApplicationContainer,
    settings: Settings,
    inspection: CommandInspection,
    run: Callable[[str | None, str | None], ToolResult[Any]],
) -> dict[str, Any]:
    execution = _execution(container)
    channel, session_id = _select_channel(
        context=context,
        container=container,
        settings=settings,
    )
    try:
        result = await execution.interactive_approvals.run_with_optional_approval(
            channel=channel,
            inspection=inspection,
            run=run,
            timeout_seconds=settings.mcp_execution_elicitation_timeout_seconds,
            decision_session_id=session_id,
        )
        return serialize_tool_result(result)
    except CodeHarnessError as error:
        return serialize_error(error)
    except ValueError as error:
        return serialize_error(InvalidExecutionRequestError(str(error)))
    except Exception:
        error_id = uuid4().hex
        _LOGGER.exception("Unexpected MCP execution failure (error_id=%s).", error_id)
        return serialize_error(InternalToolError("execution", error_id))


def register_execution_handlers(
    server: FastMCP,
    container: ApplicationContainer,
    settings: Settings,
) -> None:
    """Register execution tools without any approval-administration endpoint."""

    @server.tool()
    def inspect_process(
        executable: str,
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: list[str] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Inspect a structured process request without running it."""
        return _execute(
            lambda: _execution(container).inspect_process.execute(
                InspectProcessRequest(
                    executable,
                    tuple(args or ()),
                    cwd,
                    timeout_seconds,
                    max_output_bytes,
                    _capabilities(requested_capabilities),
                    reason,
                )
            ),
            "inspect_process",
        )

    @server.tool()
    async def run_process(
        executable: str,
        ctx: Context[Any, Any, Any],
        args: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: list[str] | None = None,
        reason: str | None = None,
        wait: bool = True,
    ) -> dict[str, Any]:
        """Run a structured process under the configured supervised backend."""
        try:
            capabilities = _capabilities(requested_capabilities)
            inspection = (
                _execution(container)
                .inspect_process.execute(
                    InspectProcessRequest(
                        executable=executable,
                        args=tuple(args or ()),
                        cwd=cwd,
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=max_output_bytes,
                        requested_capabilities=capabilities,
                        reason=reason,
                    )
                )
                .data
            )
        except CodeHarnessError as error:
            return serialize_error(error)
        except ValueError as error:
            return serialize_error(InvalidExecutionRequestError(str(error)))
        return await _run_with_optional_elicitation(
            context=ctx,
            container=container,
            settings=settings,
            inspection=inspection,
            run=lambda approval_id, approval_session_id: _execution(container).run_process.execute(
                RunProcessRequest(
                    executable=executable,
                    args=tuple(args or ()),
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=max_output_bytes,
                    requested_capabilities=capabilities,
                    reason=reason,
                    approval_id=approval_id,
                    approval_session_id=approval_session_id,
                    wait=wait,
                )
            ),
        )

    @server.tool()
    def get_execution(
        execution_id: str,
        include_output: bool = True,
    ) -> dict[str, Any]:
        """Poll one in-process execution by identifier."""
        return _execute(
            lambda: _execution(container).get_execution.execute(
                GetExecutionRequest(execution_id, include_output)
            ),
            "get_execution",
        )

    @server.tool()
    def terminate_execution(
        execution_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Request idempotent cancellation of an in-process execution."""
        return _execute(
            lambda: _execution(container).terminate_execution.execute(
                TerminateExecutionRequest(execution_id, reason)
            ),
            "terminate_execution",
        )

    if not settings.mcp_expose_powershell:
        return

    @server.tool()
    def inspect_powershell(
        script: str,
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: list[str] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Inspect a PowerShell 7 script without running it."""
        return _execute(
            lambda: _execution(container).inspect_powershell.execute(
                InspectPowerShellRequest(
                    script,
                    cwd,
                    timeout_seconds,
                    max_output_bytes,
                    _capabilities(requested_capabilities),
                    reason,
                )
            ),
            "inspect_powershell",
        )

    @server.tool()
    async def run_powershell(
        script: str,
        ctx: Context[Any, Any, Any],
        cwd: str = ".",
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        requested_capabilities: list[str] | None = None,
        reason: str | None = None,
        wait: bool = True,
    ) -> dict[str, Any]:
        """Run an explicitly supplied PowerShell 7 script after confirmation."""
        try:
            capabilities = _capabilities(requested_capabilities)
            inspection = (
                _execution(container)
                .inspect_powershell.execute(
                    InspectPowerShellRequest(
                        script=script,
                        cwd=cwd,
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=max_output_bytes,
                        requested_capabilities=capabilities,
                        reason=reason,
                    )
                )
                .data
            )
        except CodeHarnessError as error:
            return serialize_error(error)
        except ValueError as error:
            return serialize_error(InvalidExecutionRequestError(str(error)))
        return await _run_with_optional_elicitation(
            context=ctx,
            container=container,
            settings=settings,
            inspection=inspection,
            run=lambda approval_id, approval_session_id: _execution(
                container
            ).run_powershell.execute(
                RunPowerShellRequest(
                    script=script,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=max_output_bytes,
                    requested_capabilities=capabilities,
                    reason=reason,
                    approval_id=approval_id,
                    approval_session_id=approval_session_id,
                    wait=wait,
                )
            ),
        )


def _capabilities(values: list[str] | None) -> tuple[ExecutionCapability, ...]:
    return tuple(ExecutionCapability(item) for item in (values or ()))
