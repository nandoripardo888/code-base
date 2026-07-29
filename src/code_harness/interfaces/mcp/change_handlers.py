"""MCP handlers for isolated change sessions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

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

    def _execute(operation: Callable[[], ToolResult[Any]], response_detail: str | None) -> dict:
        return _execute_change_operation(container, operation, response_detail)

    @server.tool()
    def create_change_session(
        response_detail: str | None = None,
    ) -> dict:
        """Create an isolated change session (worktree and/or mirror)."""

        def operation() -> ToolResult[Any]:
            return ToolResult(data=changes.create_session.run())

        return _execute(operation, response_detail)

    @server.tool()
    def get_change_session(
        session_id: str,
        response_detail: str | None = None,
    ) -> dict:
        """Inspect a change session."""

        def operation() -> ToolResult[Any]:
            return ToolResult(data=changes.inspect_session.get(session_id))

        return _execute(operation, response_detail)

    @server.tool()
    def list_change_sessions(
        response_detail: str | None = None,
    ) -> dict:
        """List change sessions for the active workspace."""

        def operation() -> ToolResult[Any]:
            return ToolResult(
                data=changes.inspect_session.list(workspace_id=container.project.project_id)
            )

        return _execute(operation, response_detail)

    @server.tool()
    def get_change_diff(
        session_id: str,
        response_detail: str | None = None,
    ) -> dict:
        """Return a compact change-session diff descriptor."""

        def operation() -> ToolResult[Any]:
            return ToolResult(data=changes.inspect_session.get_diff_stub(session_id))

        return _execute(operation, response_detail)

    @server.tool()
    def prepare_change_session(
        session_id: str,
        segment_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict:
        """Prepare candidate changes for review (commit/manifest + digest)."""

        def operation() -> ToolResult[Any]:
            session, diff = changes.prepare_session.run(session_id, segment_id=segment_id)
            return ToolResult(data={"session": session, "diff": diff})

        return _execute(operation, response_detail)

    @server.tool()
    def accept_change_session(
        session_id: str,
        candidate_digest: str,
        segment_id: str | None = None,
        response_detail: str | None = None,
    ) -> dict:
        """Integrate an approved change session using the exact candidate digest.

        Human approval must already bind to this digest via the host decision
        channel when required; this tool does not expose approve/deny itself.
        """

        def operation() -> ToolResult[Any]:
            return ToolResult(
                data=changes.accept_session.run(
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
    ) -> dict:
        """Reject a change session without modifying the primary workspace."""

        def operation() -> ToolResult[Any]:
            return ToolResult(data=changes.reject_session.run(session_id, reason=reason))

        return _execute(operation, response_detail)
