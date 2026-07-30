"""MCP handlers for isolated change sessions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from code_harness.application.tools._timing import timed
from code_harness.bootstrap.container import ApplicationContainer
from code_harness.bootstrap.settings import Settings
from code_harness.domain.errors import CodeHarnessError, InternalToolError
from code_harness.domain.models.tool_result import ToolResult
from code_harness.interfaces.mcp.serializers import (
    resolve_response_detail,
    serialize_error,
    serialize_projected_result,
)


def _execute_change_operation(
    container: ApplicationContainer,
    operation: Callable[[], ToolResult[Any]],
    response_detail: str | None = None,
) -> dict[str, Any]:
    detail = resolve_response_detail(response_detail)
    try:
        return serialize_projected_result(container.with_index_state(operation()), detail)
    except CodeHarnessError as error:
        return serialize_error(error)
    except Exception:
        error_id = uuid4().hex
        return serialize_error(InternalToolError("change_session", error_id))


def _timed_tool_result(operation: Callable[[], Any]) -> ToolResult[Any]:
    data, elapsed_ms = timed(operation)
    return ToolResult(data=data, elapsed_ms=elapsed_ms)


def register_change_handlers(
    server: FastMCP,
    container: ApplicationContainer,
    settings: Settings,
) -> None:
    if not settings.mcp_expose_change_sessions:
        return
    if container.changes is None:
        return

    changes = container.changes

    def _execute(
        operation: Callable[[], ToolResult[Any]],
        response_detail: str | None,
    ) -> dict[str, Any]:
        return _execute_change_operation(container, operation, response_detail)

    @server.tool()
    def create_change_session(
        paths: list[str] | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Create an isolated change session (worktree and/or mirror)."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(
                lambda: changes.create_session.run(
                    paths=tuple(paths) if paths is not None else None
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def get_change_session(
        session_id: str,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Inspect a change session."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(lambda: changes.inspect_session.get(session_id))

        return _execute(operation, response_detail)

    @server.tool()
    def list_change_sessions(
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """List change sessions for the active workspace."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(
                lambda: changes.inspect_session.list(workspace_id=container.project.project_id)
            )

        return _execute(operation, response_detail)

    @server.tool()
    def get_change_diff(
        session_id: str,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Return a compact change-session diff descriptor."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(lambda: changes.inspect_session.get_diff(session_id))

        return _execute(operation, response_detail)

    @server.tool()
    def apply_change_patch(
        session_id: str,
        patch_text: str,
        segment_id: str | None = None,
        expected_checkpoint_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Apply a Codex-style structured patch inside an isolated change session."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(
                lambda: changes.patch_engine.apply(
                    session_id,
                    patch_text,
                    segment_id=segment_id,
                    expected_checkpoint_id=expected_checkpoint_id,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def list_change_checkpoints(
        session_id: str,
        segment_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """List patch checkpoints for a change-session segment."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(
                lambda: changes.patch_engine.list(session_id, segment_id=segment_id)
            )

        return _execute(operation, response_detail)

    @server.tool()
    def undo_change_patch(
        session_id: str,
        segment_id: str | None = None,
        expected_checkpoint_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Undo the active patch checkpoint atomically."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(
                lambda: changes.patch_engine.undo(
                    session_id,
                    segment_id=segment_id,
                    expected_checkpoint_id=expected_checkpoint_id,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def redo_change_patch(
        session_id: str,
        segment_id: str | None = None,
        expected_checkpoint_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Redo the next patch checkpoint atomically."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(
                lambda: changes.patch_engine.redo(
                    session_id,
                    segment_id=segment_id,
                    expected_checkpoint_id=expected_checkpoint_id,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def restore_change_checkpoint(
        session_id: str,
        checkpoint_id: str,
        expected_checkpoint_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Restore an active-history checkpoint atomically."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(
                lambda: changes.patch_engine.restore(
                    session_id,
                    checkpoint_id,
                    expected_checkpoint_id=expected_checkpoint_id,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def prepare_change_session(
        session_id: str,
        segment_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Prepare candidate changes for review (commit/manifest + digest)."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(lambda: _prepare_result(changes, session_id, segment_id))

        return _execute(operation, response_detail)

    @server.tool()
    def accept_change_session(
        session_id: str,
        candidate_digest: str,
        segment_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Integrate an approved change session using the exact candidate digest.

        Human approval must already bind to this digest via the host decision
        channel when required; this tool does not expose approve/deny itself.
        """

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(
                lambda: changes.accept_session.run(
                    session_id,
                    candidate_digest=candidate_digest,
                    segment_id=segment_id,
                )
            )

        return _execute(operation, response_detail)

    @server.tool()
    def reject_change_session(
        session_id: str,
        reason: str | None = None,
        response_detail: str | None = None,
    ) -> dict[str, Any]:
        """Reject a change session without modifying the primary workspace."""

        def operation() -> ToolResult[Any]:
            return _timed_tool_result(lambda: changes.reject_session.run(session_id, reason=reason))

        return _execute(operation, response_detail)


def _prepare_result(
    changes: Any,
    session_id: str,
    segment_id: str | None,
) -> dict[str, Any]:
    session, diff = changes.prepare_session.run(session_id, segment_id=segment_id)
    return {"session": session, "diff": diff}
