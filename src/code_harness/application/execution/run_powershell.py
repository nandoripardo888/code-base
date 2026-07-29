from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from code_harness.application.dto.execution_requests import RunPowerShellRequest
from code_harness.application.execution.inspect_powershell import InspectPowerShellTool
from code_harness.application.tools._timing import timed
from code_harness.domain.enums import ApprovalState, ExecutionState, PolicyDecision
from code_harness.domain.errors import (
    CodeHarnessError,
    ExecutionApprovalDeniedError,
    ExecutionApprovalRequiredError,
    ExecutionPolicyDeniedError,
    PowerShellExecutionDisabledError,
)
from code_harness.domain.models.execution import (
    CommandInspection,
    ExecutionApproval,
    ExecutionAuditStart,
    ExecutionResult,
    NormalizedPowerShellCommand,
    ProcessRunOutcome,
)
from code_harness.domain.models.tool_result import ToolResult
from code_harness.domain.protocols.command_policy import PowerShellRunner, SensitiveValueRedactor
from code_harness.domain.protocols.execution_runtime import (
    ExecutionRegistry,
    ExecutionTaskControl,
)
from code_harness.domain.protocols.execution_store import ApprovalStore, ExecutionStore


class RunPowerShellTool:
    """Inspect, authorize, audit, and run one PowerShell script."""

    def __init__(
        self,
        *,
        inspect_powershell: InspectPowerShellTool,
        runner: PowerShellRunner,
        powershell_enabled: bool,
        project_id: str | None = None,
        store: ExecutionStore | None = None,
        approvals: ApprovalStore | None = None,
        redactor: SensitiveValueRedactor | None = None,
        approval_ttl_seconds: int = 600,
        registry: ExecutionRegistry | None = None,
    ) -> None:
        self._inspect_powershell = inspect_powershell
        self._runner = runner
        self._powershell_enabled = powershell_enabled
        self._project_id = project_id
        self._store = store
        self._approvals = approvals
        self._redactor = redactor
        self._approval_ttl_seconds = approval_ttl_seconds
        self._registry = registry

    def execute(self, request: RunPowerShellRequest) -> ToolResult[ExecutionResult]:
        def run() -> ExecutionResult:
            inspection = self._authorize_request(request)
            command = NormalizedPowerShellCommand(
                script=request.script,
                cwd=inspection.cwd,
                timeout_seconds=inspection.timeout_seconds,
                max_output_bytes=inspection.max_output_bytes,
                requested_capabilities=inspection.requested_capabilities,
                reason=request.reason,
                resolved_executable=inspection.resolved_executable,
            )
            execution_id = str(uuid4())
            created_at = datetime.now(UTC).isoformat()
            initial = self._result(execution_id, inspection, state=ExecutionState.STARTING)
            if self._registry is None:
                self._start_audit(
                    execution_id,
                    inspection,
                    created_at,
                    approval_id=request.approval_id,
                    approval_session_id=request.approval_session_id,
                )
                return self._run_authorized(
                    execution_id,
                    inspection,
                    command,
                    initial,
                    control=None,
                )

            slot_index = self._registry.reserve(execution_id, initial)
            try:
                self._start_audit(
                    execution_id,
                    inspection,
                    created_at,
                    approval_id=request.approval_id,
                    approval_session_id=request.approval_session_id,
                    slot_index=slot_index,
                )
            except Exception:
                self._registry.discard(execution_id)
                raise
            try:
                self._registry.submit(
                    execution_id,
                    lambda control: self._run_authorized(
                        execution_id,
                        inspection,
                        command,
                        initial,
                        control=control,
                    ),
                    lambda error: self._failure_result(initial, error),
                    propagate_errors=request.wait,
                )
            except Exception as error:
                self._finish_start_failure(execution_id, error)
                raise
            if request.wait:
                return self._registry.wait(execution_id)
            return initial

        result, elapsed_ms = timed(run)
        return ToolResult(result, elapsed_ms)

    def _authorize_request(self, request: RunPowerShellRequest) -> CommandInspection:
        if not self._powershell_enabled:
            raise PowerShellExecutionDisabledError()
        inspection = self._inspect_powershell.execute(request).data
        if inspection.decision in {PolicyDecision.DENY, PolicyDecision.UNSUPPORTED}:
            self._audit_blocked(inspection)
            raise ExecutionPolicyDeniedError(
                "The execution policy blocked this PowerShell script before it was started.",
                decision=inspection.decision.value,
                blocks=[block.code for block in inspection.blocks],
            )
        if not inspection.approval_required:
            self._audit_blocked(inspection)
            raise ExecutionPolicyDeniedError(
                "Free PowerShell must require host approval.",
                decision=inspection.decision.value,
            )
        if request.approval_id is None:
            approval = self._request_approval(inspection)
            if approval is not None and approval.state is ApprovalState.DENIED:
                raise ExecutionApprovalDeniedError(approval.approval_id)
            raise ExecutionApprovalRequiredError(
                "This PowerShell script requires local approval before it can run.",
                approval_id=approval.approval_id if approval is not None else None,
                digest=(
                    inspection.approval_digest.value
                    if inspection.approval_digest is not None
                    else None
                ),
                expires_at=approval.expires_at if approval is not None else None,
            )
        return inspection

    def _start_audit(
        self,
        execution_id: str,
        inspection: CommandInspection,
        created_at: str,
        *,
        approval_id: str | None,
        approval_session_id: str | None,
        slot_index: int | None = None,
    ) -> None:
        if self._store is None:
            raise ExecutionApprovalRequiredError(
                "Approval storage is unavailable for this execution tool."
            )
        self._store.authorize_and_start(
            self._audit_start(
                execution_id,
                inspection,
                created_at,
                state=ExecutionState.STARTING,
                slot_index=slot_index,
                approval_session_id=approval_session_id,
            ),
            approval_id=approval_id,
        )

    def _run_authorized(
        self,
        execution_id: str,
        inspection: CommandInspection,
        command: NormalizedPowerShellCommand,
        initial: ExecutionResult,
        *,
        control: ExecutionTaskControl | None,
    ) -> ExecutionResult:
        started_at: list[str | None] = [None]

        def on_started() -> None:
            timestamp = datetime.now(UTC).isoformat()
            started_at[0] = timestamp
            if self._store is not None:
                self._store.mark_running(execution_id, started_at=timestamp)
            if control is not None:
                control.publish(
                    replace(initial, state=ExecutionState.RUNNING, started_at=timestamp)
                )

        try:
            if control is None:
                outcome = self._runner.run(command)
            else:
                outcome = self._runner.run_controlled(
                    command,
                    control=control,
                    on_started=on_started,
                )
        except Exception as error:
            self._audit_runner_failure(execution_id, error)
            raise
        return self._complete_execution(
            execution_id,
            inspection,
            outcome,
            started_at=started_at[0],
        )

    def _complete_execution(
        self,
        execution_id: str,
        inspection: CommandInspection,
        outcome: ProcessRunOutcome,
        *,
        started_at: str | None,
    ) -> ExecutionResult:
        state = (
            ExecutionState.CANCELLED
            if outcome.cancelled
            else (
                ExecutionState.TIMED_OUT
                if outcome.timed_out
                else (ExecutionState.COMPLETED if outcome.exit_code == 0 else ExecutionState.FAILED)
            )
        )
        finished_at = datetime.now(UTC).isoformat()
        stdout = self._redact(outcome.stdout) or ""
        stderr = self._redact(outcome.stderr) or ""
        stdout_hash = outcome.stdout_sha256 or sha256(outcome.stdout.encode("utf-8")).hexdigest()
        stderr_hash = outcome.stderr_sha256 or sha256(outcome.stderr.encode("utf-8")).hexdigest()
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
                error_code=(
                    "execution_cancelled"
                    if state is ExecutionState.CANCELLED
                    else ("powershell_exit_nonzero" if state is ExecutionState.FAILED else None)
                ),
                error_message=(
                    "Execution was cancelled."
                    if state is ExecutionState.CANCELLED
                    else (stderr if state is ExecutionState.FAILED else None)
                ),
            )
        warnings = (
            ("PowerShell output was truncated at the requested output limit.",)
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

    def _audit_runner_failure(self, execution_id: str, error: Exception) -> None:
        if self._store is None:
            return
        self._store.finish_execution(
            execution_id,
            state=ExecutionState.FAILED,
            finished_at=datetime.now(UTC).isoformat(),
            elapsed_ms=0,
            exit_code=None,
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_sha256=None,
            stderr_sha256=None,
            stdout_truncated=False,
            stderr_truncated=False,
            error_code=error.code.value if isinstance(error, CodeHarnessError) else "internal",
            error_message=self._redact(str(error)),
        )

    def _finish_start_failure(self, execution_id: str, error: Exception) -> None:
        if self._store is None:
            return
        try:
            self._audit_runner_failure(execution_id, error)
        except Exception:
            return

    def _failure_result(self, initial: ExecutionResult, error: Exception) -> ExecutionResult:
        return replace(
            initial,
            state=ExecutionState.FAILED,
            stderr=self._redact(str(error)) or "",
            finished_at=datetime.now(UTC).isoformat(),
            warnings=("PowerShell execution failed after it was accepted.",),
        )

    @staticmethod
    def _result(
        execution_id: str,
        inspection: CommandInspection,
        *,
        state: ExecutionState,
    ) -> ExecutionResult:
        return ExecutionResult(
            execution_id=execution_id,
            state=state,
            inspection=inspection,
            backend_guarantees=inspection.backend_guarantees,
            exit_code=None,
            stdout="",
            stderr="",
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
            elapsed_ms=0,
        )

    def _request_approval(self, inspection: CommandInspection) -> ExecutionApproval | None:
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
        slot_index: int | None = None,
        approval_session_id: str | None = None,
    ) -> ExecutionAuditStart:
        return ExecutionAuditStart(
            execution_id=execution_id,
            project_id=self._project_id or "",
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
            slot_index=slot_index,
            approval_session_id=approval_session_id,
        )

    def _summary(self, inspection: CommandInspection) -> str:
        script_hash = inspection.script_hash or "unknown"
        executable = inspection.resolved_executable or "pwsh"
        args = (f"script_sha256={script_hash}",)
        if self._redactor is None:
            return f"{executable} {args[0]}"
        return self._redactor.summarize_command(executable, args, cwd=inspection.cwd)

    def _redact(self, value: str | None) -> str | None:
        return self._redactor.redact(value) if self._redactor is not None else value
