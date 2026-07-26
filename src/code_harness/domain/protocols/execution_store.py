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
