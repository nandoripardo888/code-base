from __future__ import annotations

from code_harness.application.tools._timing import timed
from code_harness.domain.enums import ApprovalState
from code_harness.domain.errors import InvalidExecutionRequestError
from code_harness.domain.models.execution import ExecutionApproval
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.command_policy import SensitiveValueRedactor
from code_harness.domain.protocols.execution_store import ApprovalStore


class ApprovalAdminTool:
    """Trusted local approval administration. It must never be exposed through MCP."""

    def __init__(
        self,
        *,
        project_id: str,
        store: ApprovalStore,
        redact: SensitiveValueRedactor,
    ) -> None:
        self._project_id = project_id
        self._store = store
        self._redact = redact

    def list(
        self,
        *,
        state: ApprovalState | None = None,
        limit: int = 50,
    ) -> ToolResult[tuple[ExecutionApproval, ...]]:
        if not 1 <= limit <= 200:
            raise InvalidExecutionRequestError("limit must be between 1 and 200", limit=limit)
        approvals, elapsed_ms = timed(
            lambda: self._store.list_approvals(
                self._project_id,
                state=state,
                limit=limit,
            )
        )
        return ToolResult(approvals, elapsed_ms)

    def get(self, approval_id: str) -> ToolResult[ExecutionApproval]:
        approval, elapsed_ms = timed(
            lambda: self._store.get_approval(self._project_id, approval_id)
        )
        return ToolResult(approval, elapsed_ms)

    def approve(
        self,
        approval_id: str,
        *,
        reason: str | None = None,
    ) -> ToolResult[ExecutionApproval]:
        return self._decide(approval_id, ApprovalState.APPROVED, reason)

    def deny(
        self,
        approval_id: str,
        *,
        reason: str | None = None,
    ) -> ToolResult[ExecutionApproval]:
        return self._decide(approval_id, ApprovalState.DENIED, reason)

    def _decide(
        self,
        approval_id: str,
        state: ApprovalState,
        reason: str | None,
    ) -> ToolResult[ExecutionApproval]:
        if not approval_id.strip():
            raise InvalidExecutionRequestError("approval_id must not be empty")
        if reason is not None and len(reason) > 4_000:
            raise InvalidExecutionRequestError("reason must not exceed 4000 characters")
        approval, elapsed_ms = timed(
            lambda: self._store.decide_approval(
                self._project_id,
                approval_id,
                state=state,
                reason=self._redact.redact(reason),
            )
        )
        return ToolResult(approval, elapsed_ms)
