from typing import Protocol

from code_harness.domain.enums import ApprovalState, ExecutionState
from code_harness.domain.models.execution import ExecutionApproval, ExecutionAuditStart


class ApprovalStore(Protocol):
    def request_approval(
        self,
        *,
        project_id: str,
        digest: str,
        command_kind: str,
        command_summary: str,
        required_capabilities: tuple[str, ...],
        backend: str,
        policy_name: str,
        policy_version: str,
        ruleset_hash: str,
        ttl_seconds: int,
    ) -> ExecutionApproval: ...

    def list_approvals(
        self,
        project_id: str,
        *,
        state: ApprovalState | None = None,
        limit: int = 50,
    ) -> tuple[ExecutionApproval, ...]: ...

    def get_approval(self, project_id: str, approval_id: str) -> ExecutionApproval: ...

    def decide_approval(
        self,
        project_id: str,
        approval_id: str,
        *,
        state: ApprovalState,
        reason: str | None = None,
        decision_source: str = "local_admin",
        session_id: str | None = None,
    ) -> ExecutionApproval: ...

    def find_reusable_approval(
        self,
        project_id: str,
        *,
        digest: str,
        allowed_sources: tuple[str, ...],
    ) -> ExecutionApproval | None: ...

    def consume_approval(
        self,
        project_id: str,
        approval_id: str,
        *,
        digest: str,
        session_id: str | None = None,
        consumed_at: str | None = None,
        details: dict[str, object] | None = None,
    ) -> ExecutionApproval: ...


class ExecutionStore(Protocol):
    def initialize(self) -> None: ...

    def authorize_and_start(
        self,
        start: ExecutionAuditStart,
        *,
        approval_id: str | None,
    ) -> None: ...

    def record_blocked(self, start: ExecutionAuditStart, *, reason: str) -> None: ...

    def mark_running(self, execution_id: str, *, started_at: str) -> None: ...

    def record_cancellation_requested(
        self,
        execution_id: str,
        *,
        requested_at: str,
        reason: str | None,
    ) -> bool: ...

    def list_active_slots(self, project_id: str) -> tuple[tuple[str, int | None], ...]: ...

    def recover_interrupted(self, execution_id: str, *, finished_at: str) -> bool: ...

    def finish_execution(
        self,
        execution_id: str,
        *,
        state: ExecutionState,
        finished_at: str,
        elapsed_ms: int,
        exit_code: int | None,
        stdout_bytes: int,
        stderr_bytes: int,
        stdout_sha256: str | None,
        stderr_sha256: str | None,
        stdout_truncated: bool,
        stderr_truncated: bool,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None: ...
