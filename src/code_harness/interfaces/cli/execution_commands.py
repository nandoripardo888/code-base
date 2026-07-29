"""CLI commands for supervised execution and trusted local approval."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from code_harness.application.dto.execution_requests import (
    InspectPowerShellRequest,
    InspectProcessRequest,
    RunPowerShellRequest,
    RunProcessRequest,
)
from code_harness.domain.enums import ApprovalState, ExecutionCapability
from code_harness.domain.errors import ExecutionDisabledError, InvalidQueryError
from code_harness.domain.models.execution import (
    CommandInspection,
    ExecutionApproval,
    ExecutionResult,
)
from code_harness.domain.models.tool_result import ToolResult
from code_harness.infrastructure.filesystem import PathGuard

if TYPE_CHECKING:
    from code_harness.bootstrap.execution import ExecutionContainer
    from code_harness.interfaces.cli.main import CliState

execution_app = typer.Typer(
    help="Inspect and run commands under the configured execution policy.",
    no_args_is_help=True,
)
approvals_app = typer.Typer(
    help="Manage local single-use execution approvals.",
    no_args_is_help=True,
)
execution_app.add_typer(approvals_app, name="approvals")


def _parse_capabilities(raw: list[str] | None) -> tuple[ExecutionCapability, ...]:
    if not raw:
        return ()
    return tuple(ExecutionCapability(item) for item in raw)


@execution_app.command(
    "inspect-process",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def inspect_process(
    ctx: typer.Context,
    executable: Annotated[str, typer.Argument(help="Program to inspect (not a shell line).")],
    cwd: Annotated[str, typer.Option("--cwd", help="Working directory inside the project.")] = ".",
    timeout_seconds: Annotated[
        float | None,
        typer.Option("--timeout-seconds", help="Requested timeout for a future run."),
    ] = None,
    max_output_bytes: Annotated[
        int | None,
        typer.Option("--max-output-bytes", help="Requested output limit for a future run."),
    ] = None,
    capability: Annotated[
        list[str] | None,
        typer.Option("--capability", help="Requested capability (repeatable)."),
    ] = None,
    reason: Annotated[
        str | None, typer.Option("--reason", help="Why the command is needed.")
    ] = None,
) -> None:
    """Inspect a structured process request without starting it.

    Arguments after the executable are forwarded as-is, including flags like
    ``--short``. Example: ``code-harness execution inspect-process git status --short``.
    """
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj
    process_args = tuple(ctx.args)

    def operation() -> ToolResult[CommandInspection]:
        container = state.container()
        if container.execution is None:
            raise ExecutionDisabledError()
        request = InspectProcessRequest(
            executable,
            process_args,
            cwd,
            timeout_seconds,
            max_output_bytes,
            _parse_capabilities(capability),
            reason,
        )
        return container.execution.inspect_process.execute(request)

    _execute(state, operation)


@execution_app.command(
    "run-process",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run_process(
    ctx: typer.Context,
    executable: Annotated[str, typer.Argument(help="Allowed program to execute without a shell.")],
    cwd: Annotated[str, typer.Option("--cwd", help="Working directory inside the project.")] = ".",
    timeout_seconds: Annotated[float | None, typer.Option("--timeout-seconds")] = None,
    max_output_bytes: Annotated[int | None, typer.Option("--max-output-bytes")] = None,
    capability: Annotated[list[str] | None, typer.Option("--capability")] = None,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    approval_id: Annotated[
        str | None,
        typer.Option("--approval-id", help="Approved single-use request ID."),
    ] = None,
) -> None:
    """Run an autoallowed or locally approved process under Windows supervision."""
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj
    process_args = tuple(ctx.args)

    def operation() -> ToolResult[ExecutionResult]:
        container = state.container()
        if container.execution is None:
            raise ExecutionDisabledError()
        return container.execution.run_process.execute(
            RunProcessRequest(
                executable=executable,
                args=process_args,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                requested_capabilities=_parse_capabilities(capability),
                reason=reason,
                approval_id=approval_id,
            )
        )

    _execute(state, operation)


@approvals_app.command("list")
def list_approvals(
    ctx: typer.Context,
    state_filter: Annotated[
        str | None,
        typer.Option("--state", help="Filter by approval state."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
) -> None:
    """List approvals for the active project."""
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation() -> ToolResult[tuple[ExecutionApproval, ...]]:
        container = state.container()
        if container.execution is None:
            raise ExecutionDisabledError()
        selected = ApprovalState(state_filter) if state_filter is not None else None
        return container.execution.approvals.list(state=selected, limit=limit)

    _execute(state, operation)


@approvals_app.command("show")
def show_approval(
    ctx: typer.Context,
    approval_id: Annotated[str, typer.Argument(help="Approval ID.")],
) -> None:
    """Show one sanitized approval request."""
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj
    _execute(
        state,
        lambda: _approval_container(state).approvals.get(approval_id),
    )


@approvals_app.command("approve")
def approve_execution(
    ctx: typer.Context,
    approval_id: Annotated[str, typer.Argument(help="Approval ID.")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Approve one exact command for one use."""
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj
    _execute(
        state,
        lambda: _approval_container(state).approvals.approve(
            approval_id,
            reason=reason,
        ),
    )


@approvals_app.command("deny")
def deny_execution(
    ctx: typer.Context,
    approval_id: Annotated[str, typer.Argument(help="Approval ID.")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Deny one exact command request."""
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj
    _execute(
        state,
        lambda: _approval_container(state).approvals.deny(
            approval_id,
            reason=reason,
        ),
    )


def _approval_container(state: CliState) -> ExecutionContainer:
    container = state.container()
    if container.execution is None:
        raise ExecutionDisabledError()
    return container.execution


@execution_app.command("inspect-powershell")
def inspect_powershell(
    ctx: typer.Context,
    script: Annotated[
        str | None,
        typer.Option("--script", help="PowerShell script text to inspect."),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", help="Read script text from a file under the project."),
    ] = None,
    cwd: Annotated[str, typer.Option("--cwd", help="Working directory inside the project.")] = ".",
    timeout_seconds: Annotated[
        float | None,
        typer.Option("--timeout-seconds", help="Requested timeout for a future run."),
    ] = None,
    max_output_bytes: Annotated[
        int | None,
        typer.Option("--max-output-bytes", help="Requested output limit for a future run."),
    ] = None,
    capability: Annotated[
        list[str] | None,
        typer.Option("--capability", help="Requested capability (repeatable)."),
    ] = None,
    reason: Annotated[
        str | None, typer.Option("--reason", help="Why the script is needed.")
    ] = None,
) -> None:
    """Inspect a PowerShell script without executing it."""
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation() -> ToolResult[CommandInspection]:
        container = state.container()
        if container.execution is None:
            raise ExecutionDisabledError()
        script_text = _load_powershell_script(container.project.root, script=script, file=file)
        request = InspectPowerShellRequest(
            script_text,
            cwd,
            timeout_seconds,
            max_output_bytes,
            _parse_capabilities(capability),
            reason,
        )
        return container.execution.inspect_powershell.execute(request)

    _execute(state, operation)


@execution_app.command("run-powershell")
def run_powershell(
    ctx: typer.Context,
    script: Annotated[
        str | None,
        typer.Option("--script", help="PowerShell script text to execute."),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", help="Read script text from a file under the project."),
    ] = None,
    cwd: Annotated[str, typer.Option("--cwd", help="Working directory inside the project.")] = ".",
    timeout_seconds: Annotated[float | None, typer.Option("--timeout-seconds")] = None,
    max_output_bytes: Annotated[int | None, typer.Option("--max-output-bytes")] = None,
    capability: Annotated[list[str] | None, typer.Option("--capability")] = None,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    approval_id: Annotated[
        str | None,
        typer.Option("--approval-id", help="Approved single-use request ID."),
    ] = None,
) -> None:
    """Run an approved PowerShell 7 script under Windows supervision."""
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation() -> ToolResult[ExecutionResult]:
        container = state.container()
        if container.execution is None:
            raise ExecutionDisabledError()
        script_text = _load_powershell_script(container.project.root, script=script, file=file)
        return container.execution.run_powershell.execute(
            RunPowerShellRequest(
                script=script_text,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                requested_capabilities=_parse_capabilities(capability),
                reason=reason,
                approval_id=approval_id,
            )
        )

    _execute(state, operation)


def _load_powershell_script(
    project_root: str,
    *,
    script: str | None,
    file: Path | None,
) -> str:
    if file is not None and script is not None:
        raise InvalidQueryError("Provide either --script or --file, not both.")
    if file is not None:
        absolute, _relative = PathGuard(project_root).resolve_within_root(
            str(file),
            expected_kind="file",
            must_exist=True,
        )
        return Path(absolute).read_text(encoding="utf-8")
    if script is not None:
        return script
    raise InvalidQueryError("Provide --script or --file.")
