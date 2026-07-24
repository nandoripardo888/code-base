"""CLI commands for execution inspection (E0 - no run/approve)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from code_harness.application.dto.execution_requests import (
    InspectPowerShellRequest,
    InspectProcessRequest,
)
from code_harness.domain.enums import ExecutionCapability
from code_harness.domain.errors import ExecutionDisabledError, InvalidQueryError
from code_harness.domain.models.execution import CommandInspection
from code_harness.domain.models.tool_result import ToolResult
from code_harness.infrastructure.filesystem import PathGuard

execution_app = typer.Typer(
    help="Inspect proposed commands without executing them.",
    no_args_is_help=True,
)


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
    from code_harness.interfaces.cli.main import CliState, _execute

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
    from code_harness.interfaces.cli.main import CliState, _execute

    state: CliState = ctx.obj

    def operation() -> ToolResult[CommandInspection]:
        container = state.container()
        if container.execution is None:
            raise ExecutionDisabledError()
        if file is not None and script is not None:
            raise InvalidQueryError("Provide either --script or --file, not both.")
        if file is not None:
            absolute, _relative = PathGuard(container.project.root).resolve_within_root(
                str(file),
                expected_kind="file",
                must_exist=True,
            )
            script_text = Path(absolute).read_text(encoding="utf-8")
        elif script is not None:
            script_text = script
        else:
            raise InvalidQueryError("Provide --script or --file.")
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
