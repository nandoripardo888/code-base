"""Opt-in MCP adapter for supervised command execution."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import weakref
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

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
from code_harness.domain.enums import ApprovalState, ExecutionCapability
from code_harness.domain.errors import (
    CodeHarnessError,
    ExecutionApprovalDeniedError,
    ExecutionApprovalRequiredError,
    InternalToolError,
    InvalidExecutionRequestError,
)
from code_harness.domain.models.execution import CommandInspection, ExecutionApproval
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.serialization import serialize_error, serialize_tool_result

_LOGGER = logging.getLogger(__name__)
_CONFIRMATION_LOCKS: dict[str, threading.Lock] = {}
_CONFIRMATION_LOCKS_GUARD = threading.Lock()
_SESSION_IDS: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
_SESSION_IDS_GUARD = threading.Lock()


class ExecutionConfirmation(BaseModel):
    """Primitive-only form returned by an interactive MCP client."""

    decision: str = Field(description="Approve this exact one-time execution, or decline it.")


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
    if not settings.mcp_execution_elicitation_enabled:
        return False
    if settings.mcp_execution_elicitation_trust_mode != "local_interactive":
        return False
    required = types.ClientCapabilities(
        elicitation=types.ElicitationCapability(form=types.FormElicitationCapability())
    )
    return bool(context.session.check_client_capability(required))


def _capabilities(values: list[str] | None) -> tuple[ExecutionCapability, ...]:
    return tuple(ExecutionCapability(item) for item in (values or ()))


def _approved_for_digest(
    container: ApplicationContainer,
    digest: str | None,
) -> ExecutionApproval | None:
    if digest is None:
        return None
    approvals = (
        _execution(container)
        .approvals.list(
            state=ApprovalState.APPROVED,
            limit=200,
        )
        .data
    )
    return next(
        (
            item
            for item in approvals
            if item.digest == digest and item.decision_source != "mcp_elicitation"
        ),
        None,
    )


def _claim_confirmation(approval_id: str) -> threading.Lock | None:
    with _CONFIRMATION_LOCKS_GUARD:
        lock = _CONFIRMATION_LOCKS.setdefault(approval_id, threading.Lock())
        return lock if lock.acquire(blocking=False) else None


def _release_confirmation(approval_id: str, lock: threading.Lock) -> None:
    lock.release()
    with _CONFIRMATION_LOCKS_GUARD:
        if not lock.locked():
            _CONFIRMATION_LOCKS.pop(approval_id, None)


def _session_id(context: Context[Any, Any, Any]) -> str:
    session = context.session
    with _SESSION_IDS_GUARD:
        value = _SESSION_IDS.get(session)
        if value is None:
            value = uuid4().hex
            _SESSION_IDS[session] = value
        return value


def _confirmation_message(
    inspection: CommandInspection,
    *,
    project_id: str,
    redact: Callable[[str | None], str | None],
) -> str:
    def safe(value: str) -> str:
        return json.dumps(redact(value) or "", ensure_ascii=True)

    command = (
        json.dumps(
            [
                redact(inspection.executable) or "",
                *(redact(argument) or "" for argument in inspection.args),
            ],
            ensure_ascii=True,
        )
        if inspection.executable
        else safe(f"PowerShell script sha256:{inspection.script_hash}")
    )
    capabilities = ", ".join(item.value for item in inspection.required_capabilities) or "none"
    risks = "; ".join(f"{item.severity.value}: {item.message}" for item in inspection.risks)
    digest = inspection.approval_digest.value if inspection.approval_digest else ""
    return (
        "Confirm a one-time supervised execution.\n"
        f"Project: {safe(project_id)}\n"
        f"Command: {command}\n"
        f"Working directory: {safe(inspection.cwd)}\n"
        f"Backend: {safe(inspection.backend)}\n"
        f"Capabilities: {safe(capabilities)}\n"
        f"Limits: timeout={inspection.timeout_seconds}s, "
        f"output={inspection.max_output_bytes} bytes\n"
        f"Risks: {safe(risks or 'none reported')}\n"
        f"Policy: {safe(f'{inspection.policy_name}/{inspection.policy_version}')} "
        f"ruleset={safe(inspection.ruleset_hash)}\n"
        f"Canonical digest: {safe(digest)}"
    )


async def _run_with_optional_elicitation(
    *,
    context: Context[Any, Any, Any],
    container: ApplicationContainer,
    settings: Settings,
    inspection: CommandInspection,
    run: Callable[[str | None, str | None], ToolResult[Any]],
) -> dict[str, Any]:
    approved = _approved_for_digest(
        container,
        inspection.approval_digest.value if inspection.approval_digest else None,
    )
    try:
        return serialize_tool_result(run(approved.approval_id if approved else None, None))
    except ExecutionApprovalRequiredError as required:
        if not _can_elicit(context, settings):
            return serialize_error(required)
        approval_id = required.details.get("approval_id")
        if not isinstance(approval_id, str):
            return serialize_error(required)
        confirmation_lock = _claim_confirmation(approval_id)
        if confirmation_lock is None:
            required.details["elicitation_action"] = "already_in_progress"
            return serialize_error(required)
        try:
            try:
                response = await asyncio.wait_for(
                    context.elicit(
                        _confirmation_message(
                            inspection,
                            project_id=settings.project.project_id,
                            redact=_execution(container).redactor.redact,
                        ),
                        ExecutionConfirmation,
                    ),
                    timeout=settings.mcp_execution_elicitation_timeout_seconds,
                )
            except (TimeoutError, asyncio.CancelledError):
                required.details["elicitation_action"] = "timeout"
                return serialize_error(required)
            except Exception:
                _LOGGER.info("MCP execution elicitation became unavailable.", exc_info=True)
                required.details["elicitation_action"] = "unavailable"
                return serialize_error(required)

            session_id = _session_id(context)
            action = response.action
            if action == "accept":
                decision = getattr(getattr(response, "data", None), "decision", None)
                if decision == "approve":
                    _execution(container).approvals.approve(
                        approval_id,
                        reason=f"mcp_elicitation session={session_id}",
                        decision_source="mcp_elicitation",
                        session_id=session_id,
                    )
                    return serialize_tool_result(run(approval_id, session_id))
                _execution(container).approvals.deny(
                    approval_id,
                    reason=f"mcp_elicitation_declined session={session_id}",
                    decision_source="mcp_elicitation",
                    session_id=session_id,
                )
                return serialize_error(ExecutionApprovalDeniedError(approval_id))
            if action == "decline":
                _execution(container).approvals.deny(
                    approval_id,
                    reason=f"mcp_elicitation_declined session={session_id}",
                    decision_source="mcp_elicitation",
                    session_id=session_id,
                )
                return serialize_error(ExecutionApprovalDeniedError(approval_id))
            required.details["elicitation_action"] = "cancel"
            return serialize_error(required)
        finally:
            _release_confirmation(approval_id, confirmation_lock)
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
