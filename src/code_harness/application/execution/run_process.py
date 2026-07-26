from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from code_harness.application.dto.execution_requests import RunProcessRequest
from code_harness.application.execution.inspect_process import InspectProcessTool
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import ApprovalState, ExecutionState, PolicyDecision
from code_harness.domain.errors import (
    CodeHarnessError,
    ExecutionApprovalDeniedError,
    ExecutionApprovalRequiredError,
    ExecutionPolicyDeniedError,
)
from code_harness.domain.models.execution import (
    CommandInspection,
    ExecutionApproval,
    ExecutionAuditStart,
    ExecutionResult,
    NormalizedProcessCommand,
)
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.command_policy import (
    ProcessRunner,
    SensitiveValueRedactor,
)
from code_harness.domain.protocols.execution_store import ApprovalStore, ExecutionStore


class RunProcessTool:
    """Inspect, authorize, audit, and synchronously run one structured process."""

    def __init__(
        self,
        *,
        inspect_process: InspectProcessTool,
        runner: ProcessRunner,
        project_id: str | None = None,
        store: ExecutionStore | None = None,
        approvals: ApprovalStore | None = None,
        redactor: SensitiveValueRedactor | None = None,
        approval_ttl_seconds: int = 600,
        require_approval: bool = False,
    ) -> None:
        self._inspect_process = inspect_process
        self._runner = runner
        self._project_id = project_id
        self._store = store
        self._approvals = approvals
        self._redactor = redactor
        self._approval_ttl_seconds = approval_ttl_seconds
        self._require_approval = require_approval

    def execute(self, request: RunProcessRequest) -> ToolResult[ExecutionResult]:
        def run() -> ExecutionResult:
            inspection = self._inspect_process.execute(request).data
            if inspection.decision in {PolicyDecision.DENY, PolicyDecision.UNSUPPORTED}:
                self._audit_blocked(inspection)
                raise ExecutionPolicyDeniedError(
                    "The execution policy blocked this command before it was started.",
                    decision=inspection.decision.value,
                    blocks=[block.code for block in inspection.blocks],
                )

            approval_id = request.approval_id
            approval_needed = inspection.approval_required or self._require_approval
            if approval_needed and approval_id is None:
                approval = self._request_approval(inspection)
                if approval is not None and approval.state is ApprovalState.DENIED:
                    raise ExecutionApprovalDeniedError(approval.approval_id)
                raise ExecutionApprovalRequiredError(
                    "This command requires local approval before it can run.",
                    approval_id=approval.approval_id if approval is not None else None,
                    digest=(
                        inspection.approval_digest.value
                        if inspection.approval_digest is not None
                        else None
                    ),
                    expires_at=approval.expires_at if approval is not None else None,
                )
            if inspection.decision is not PolicyDecision.ALLOW and not inspection.approval_required:
                self._audit_blocked(inspection)
                raise ExecutionPolicyDeniedError(
                    "The execution policy did not authorize this command.",
                    decision=inspection.decision.value,
                )

            command = NormalizedProcessCommand(
                executable=inspection.resolved_executable
                or inspection.executable
                or request.executable,
                args=inspection.args,
                cwd=inspection.cwd,
                timeout_seconds=inspection.timeout_seconds,
                max_output_bytes=inspection.max_output_bytes,
                requested_capabilities=inspection.requested_capabilities,
                reason=request.reason,
                resolved_executable=inspection.resolved_executable,
            )
            execution_id = str(uuid4())
            started_at = datetime.now(UTC).isoformat()
            start = self._audit_start(
                execution_id,
                inspection,
                started_at,
                state=ExecutionState.STARTING,
            )
            if self._store is not None:
                self._store.authorize_and_start(start, approval_id=approval_id)
            elif approval_id is not None:
                raise ExecutionApprovalRequiredError(
                    "Approval storage is unavailable for this execution tool."
                )

            try:
                outcome = self._runner.run(command)
            except Exception as error:
                finished_at = datetime.now(UTC).isoformat()
                if self._store is not None:
                    message = self._redact(str(error))
                    self._store.finish_execution(
                        execution_id,
                        state=ExecutionState.FAILED,
                        finished_at=finished_at,
                        elapsed_ms=0,
                        exit_code=None,
                        stdout_bytes=0,
                        stderr_bytes=0,
                        stdout_sha256=None,
                        stderr_sha256=None,
                        stdout_truncated=False,
                        stderr_truncated=False,
                        error_code=(
                            error.code.value if isinstance(error, CodeHarnessError) else "internal"
                        ),
                        error_message=message,
                    )
                raise

            state = (
                ExecutionState.TIMED_OUT
                if outcome.timed_out
                else (ExecutionState.COMPLETED if outcome.exit_code == 0 else ExecutionState.FAILED)
            )
            finished_at = datetime.now(UTC).isoformat()
            stdout = self._redact(outcome.stdout) or ""
            stderr = self._redact(outcome.stderr) or ""
            stdout_hash = (
                outcome.stdout_sha256 or sha256(outcome.stdout.encode("utf-8")).hexdigest()
            )
            stderr_hash = (
                outcome.stderr_sha256 or sha256(outcome.stderr.encode("utf-8")).hexdigest()
            )
            if self._store is not None:
                self._store.finish_execution(
                    execution_id,
                    state=state,
                    finished_at=finished_at,
                    elapsed_ms=outcome.elapsed_ms,
                    exit_code=outcome.exit_code,
                    stdout_bytes=outcome.stdout_bytes,
                    stderr_bytes=outcome.stderr_bytes,
                    stdout_sha256=stdout_hash,
                    stderr_sha256=stderr_hash,
                    stdout_truncated=outcome.stdout_truncated,
                    stderr_truncated=outcome.stderr_truncated,
                    error_code="process_exit_nonzero" if state is ExecutionState.FAILED else None,
                    error_message=stderr if state is ExecutionState.FAILED else None,
                )
            warnings = (
                ("Process output was truncated at the requested output limit.",)
                if outcome.stdout_truncated or outcome.stderr_truncated
                else ()
            )
            return ExecutionResult(
                execution_id=execution_id,
                state=state,
                inspection=inspection,
                backend_guarantees=inspection.backend_guarantees,
                exit_code=outcome.exit_code,
                stdout=stdout,
                stderr=stderr,
                stdout_bytes=outcome.stdout_bytes,
                stderr_bytes=outcome.stderr_bytes,
                stdout_truncated=outcome.stdout_truncated,
                stderr_truncated=outcome.stderr_truncated,
                elapsed_ms=outcome.elapsed_ms,
                started_at=started_at,
                finished_at=finished_at,
                warnings=warnings,
            )

        result, elapsed_ms = timed(run)
        return ToolResult(result, elapsed_ms)

    def _request_approval(
        self,
        inspection: CommandInspection,
    ) -> ExecutionApproval | None:
        if (
            self._approvals is None
            or self._project_id is None
            or inspection.approval_digest is None
        ):
            return None
        return self._approvals.request_approval(
            project_id=self._project_id,
            digest=inspection.approval_digest.value,
            command_kind=inspection.kind.value,
            command_summary=self._summary(inspection),
            required_capabilities=tuple(item.value for item in inspection.required_capabilities),
            backend=inspection.backend,
            policy_name=inspection.policy_name,
            policy_version=inspection.policy_version,
            ruleset_hash=inspection.ruleset_hash,
            ttl_seconds=self._approval_ttl_seconds,
        )

    def _audit_blocked(self, inspection: CommandInspection) -> None:
        if self._store is None or self._project_id is None:
            return
        timestamp = datetime.now(UTC).isoformat()
        start = self._audit_start(
            str(uuid4()),
            inspection,
            timestamp,
            state=ExecutionState.BLOCKED,
        )
        reason = "; ".join(block.code for block in inspection.blocks) or inspection.decision.value
        self._store.record_blocked(start, reason=self._redact(reason) or "")

    def _audit_start(
        self,
        execution_id: str,
        inspection: CommandInspection,
        created_at: str,
        *,
        state: ExecutionState,
    ) -> ExecutionAuditStart:
        project_id = self._project_id or ""
        return ExecutionAuditStart(
            execution_id=execution_id,
            project_id=project_id,
            digest=(
                inspection.approval_digest.value if inspection.approval_digest is not None else None
            ),
            state=state,
            command_kind=inspection.kind,
            command_summary=self._summary(inspection),
            requested_capabilities=inspection.requested_capabilities,
            required_capabilities=inspection.required_capabilities,
            backend=inspection.backend,
            backend_guarantees_json=json.dumps(
                asdict(inspection.backend_guarantees),
                separators=(",", ":"),
                sort_keys=True,
            ),
            policy_decision=inspection.decision,
            policy_name=inspection.policy_name,
            policy_version=inspection.policy_version,
            ruleset_hash=inspection.ruleset_hash,
            created_at=created_at,
        )

    def _summary(self, inspection: CommandInspection) -> str:
        executable = inspection.executable or inspection.kind.value
        if self._redactor is None:
            return f"{executable} {' '.join(inspection.args)}".strip()
        return self._redactor.summarize_command(
            executable,
            inspection.args,
            cwd=inspection.cwd,
        )

    def _redact(self, value: str | None) -> str | None:
        return self._redactor.redact(value) if self._redactor is not None else value
