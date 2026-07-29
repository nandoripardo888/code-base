from __future__ import annotations

from datetime import UTC, datetime

from code_harness.domain.enums import ApprovalState
from code_harness.domain.errors import (
    ExecutionApprovalDeniedError,
    ExecutionApprovalRequiredError,
)
from code_harness.domain.protocols.execution_store import ApprovalStore


class ReviewActionAuthorizer:
    """Bind non-process review actions to ExecutionApproval digests."""

    def __init__(
        self,
        *,
        project_id: str,
        store: ApprovalStore | None,
        require_approval: bool,
        approval_ttl_seconds: int,
        backend: str = "host_supervised",
        policy_name: str = "review_actions_v1",
        policy_version: str = "1",
    ) -> None:
        self._project_id = project_id
        self._store = store
        self._require_approval = require_approval
        self._approval_ttl_seconds = approval_ttl_seconds
        self._backend = backend
        self._policy_name = policy_name
        self._policy_version = policy_version

    def ensure_authorized(
        self,
        *,
        digest: str,
        action_kind: str,
        summary: str,
        approval_id: str | None,
        approval_session_id: str | None = None,
        required_capabilities: tuple[str, ...] = (),
    ) -> str | None:
        if not self._require_approval:
            return None
        if self._store is None:
            raise ExecutionApprovalRequiredError(
                "Approval storage is unavailable for this review action.",
                digest=digest,
            )
        if approval_id is None:
            approval = self._store.request_approval(
                project_id=self._project_id,
                digest=digest,
                command_kind=action_kind,
                command_summary=summary,
                required_capabilities=required_capabilities,
                backend=self._backend,
                policy_name=self._policy_name,
                policy_version=self._policy_version,
                ruleset_hash="",
                ttl_seconds=self._approval_ttl_seconds,
            )
            if approval.state is ApprovalState.DENIED:
                raise ExecutionApprovalDeniedError(approval.approval_id)
            raise ExecutionApprovalRequiredError(
                "This review action requires local approval before it can run.",
                approval_id=approval.approval_id,
                digest=digest,
                expires_at=approval.expires_at,
            )
        consumed = self._store.consume_approval(
            self._project_id,
            approval_id,
            digest=digest,
            session_id=approval_session_id,
            consumed_at=datetime.now(UTC).isoformat(),
        )
        return consumed.approval_id
