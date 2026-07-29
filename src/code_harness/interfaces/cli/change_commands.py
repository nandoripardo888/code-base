"""CLI commands for isolated change sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from code_harness.interfaces.cli.main import CliState

changes_app = typer.Typer(
    help="Manage isolated change sessions (worktree/mirror).",
    no_args_is_help=True,
)


@changes_app.command("start")
def changes_start(ctx: typer.Context) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        session = state.container().changes.create_session.run()
        return ToolResult(data=session)

    _execute(state, operation)


@changes_app.command("status")
def changes_status(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument(help="Change session ID.")],
) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        return ToolResult(data=state.container().changes.inspect_session.get(session_id))

    _execute(state, operation)


@changes_app.command("list")
def changes_list(ctx: typer.Context) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        sessions = state.container().changes.inspect_session.list(
            workspace_id=state.container().project.project_id,
        )
        return ToolResult(data=sessions)

    _execute(state, operation)


@changes_app.command("diff")
def changes_diff(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument(help="Change session ID.")],
) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        return ToolResult(data=state.container().changes.inspect_session.get_diff_stub(session_id))

    _execute(state, operation)


@changes_app.command("prepare")
def changes_prepare(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument(help="Change session ID.")],
    segment_id: Annotated[str | None, typer.Option("--segment-id")] = None,
) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        session, diff = state.container().changes.prepare_session.run(
            session_id,
            segment_id=segment_id,
        )
        return ToolResult(data={"session": session, "diff": diff})

    _execute(state, operation)


@changes_app.command("accept")
def changes_accept(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument(help="Change session ID.")],
    digest: Annotated[str, typer.Option("--digest", help="Exact candidate digest.")],
    segment_id: Annotated[str | None, typer.Option("--segment-id")] = None,
) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        session = state.container().changes.accept_session.run(
            session_id,
            candidate_digest=digest,
            segment_id=segment_id,
        )
        return ToolResult(data=session)

    _execute(state, operation)


@changes_app.command("reject")
def changes_reject(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument(help="Change session ID.")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        return ToolResult(
            data=state.container().changes.reject_session.run(session_id, reason=reason)
        )

    _execute(state, operation)


@changes_app.command("cleanup")
def changes_cleanup(
    ctx: typer.Context,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    expired: Annotated[bool, typer.Option("--expired")] = False,
) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        return ToolResult(
            data=state.container().changes.retention.run(dry_run=dry_run, expired_only=expired)
        )

    _execute(state, operation)


@changes_app.command("recover")
def changes_recover(
    ctx: typer.Context,
    session_id: Annotated[str, typer.Argument(help="Change session ID.")],
) -> None:
    from code_harness.domain.models.tool_result import ToolResult
    from code_harness.interfaces.cli.main import _execute

    state: CliState = ctx.obj

    def operation():
        assert state.container().changes is not None
        return ToolResult(data=state.container().changes.recovery.recover(session_id))

    _execute(state, operation)
