import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from code_harness.application.dto.execution_requests import (
    GetExecutionRequest,
    RunPowerShellRequest,
    RunProcessRequest,
    TerminateExecutionRequest,
)
from code_harness.application.execution import (
    GetExecutionTool,
    RunPowerShellTool,
    RunProcessTool,
    TerminateExecutionTool,
)
from code_harness.bootstrap.execution import _recover_interrupted
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import (
    ApprovalState,
    CommandKind,
    ExecutionState,
    PolicyDecision,
)
from code_harness.domain.errors import (
    ExecutionConcurrencyLimitError,
    ExecutionNotFoundError,
)
from code_harness.domain.models.execution import (
    ApprovalDigest,
    BackendGuarantees,
    CommandInspection,
    ExecutionAuditStart,
    ExecutionResult,
    ProcessRunOutcome,
)
from code_harness.domain.models.tool_result import ToolResult
from code_harness.infrastructure.execution.persistence import SQLiteExecutionStore
from code_harness.infrastructure.execution.redaction import SensitiveDataRedactor
from code_harness.infrastructure.execution.runners import (
    ProcessRegistry,
    ProjectExecutionLimiter,
)


def _guarantees() -> BackendGuarantees:
    return BackendGuarantees(
        backend="host_supervised",
        execution_available=True,
        process_tree_containment=True,
        timeout_enforced=True,
        output_limit_enforced=True,
        filesystem_isolated=False,
        network_isolated=False,
        credentials_isolated=False,
    )


def _inspection(*, approval_required: bool = False) -> CommandInspection:
    return CommandInspection(
        kind=CommandKind.PROCESS,
        decision=(PolicyDecision.APPROVAL_REQUIRED if approval_required else PolicyDecision.ALLOW),
        requested_capabilities=(),
        required_capabilities=(),
        approval_required=approval_required,
        reasons=(),
        risks=(),
        blocks=(),
        approval_digest=ApprovalDigest("digest-e4"),
        cwd="C:/project",
        timeout_seconds=30,
        max_output_bytes=1_000,
        backend_guarantees=_guarantees(),
        executable="git",
        resolved_executable="C:/Program Files/Git/cmd/git.exe",
        args=("status", "--short"),
        ruleset_hash="rules-e4",
    )


def _powershell_inspection() -> CommandInspection:
    return replace(
        _inspection(approval_required=True),
        kind=CommandKind.POWERSHELL,
        executable="pwsh",
        resolved_executable="C:/Program Files/PowerShell/7/pwsh.exe",
        args=(),
        script_hash="script-e4",
    )


def _result(
    execution_id: str,
    *,
    state: ExecutionState = ExecutionState.STARTING,
    stdout: str = "",
) -> ExecutionResult:
    return ExecutionResult(
        execution_id=execution_id,
        state=state,
        inspection=_inspection(),
        backend_guarantees=_guarantees(),
        exit_code=0 if state is ExecutionState.COMPLETED else None,
        stdout=stdout,
        stderr="",
        stdout_bytes=len(stdout),
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        elapsed_ms=1,
    )


class _Inspect:
    def __init__(self, inspection: CommandInspection) -> None:
        self.inspection = inspection

    def execute(self, _request: object) -> ToolResult[CommandInspection]:
        return ToolResult(self.inspection, 0)


class _ControlledRunner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, _command: object) -> ProcessRunOutcome:
        raise AssertionError("E4 must use the controlled runner path")

    def run_controlled(self, _command, *, control, on_started):  # type: ignore[no-untyped-def]
        def terminator() -> None:
            self.release.set()

        control.register_terminator(terminator)
        on_started()
        self.started.set()
        assert self.release.wait(timeout=5)
        cancelled = control.cancellation_requested
        control.clear_terminator()
        return ProcessRunOutcome(
            exit_code=None if cancelled else 0,
            stdout="token=[REDACTED]",
            stderr="",
            stdout_bytes=16,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            elapsed_ms=2,
            cancelled=cancelled,
        )


def _audit_start(execution_id: str, *, slot_index: int | None = 0) -> ExecutionAuditStart:
    return ExecutionAuditStart(
        execution_id=execution_id,
        project_id="project-e4",
        digest=None,
        state=ExecutionState.STARTING,
        command_kind=CommandKind.PROCESS,
        command_summary="git status",
        requested_capabilities=(),
        required_capabilities=(),
        backend="host_supervised",
        backend_guarantees_json="{}",
        policy_decision=PolicyDecision.ALLOW,
        policy_name="deterministic_v1",
        policy_version="1",
        ruleset_hash="rules-e4",
        created_at=datetime.now(UTC).isoformat(),
        slot_index=slot_index,
    )


def test_settings_and_e4_requests_validate_new_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_MAX_CONCURRENT", "3")
    settings = Settings.for_root(tmp_path)
    assert settings.execution_max_concurrent == 3
    assert RunProcessRequest("git").wait is True
    assert GetExecutionRequest("execution").include_output is True
    assert TerminateExecutionRequest("execution", "reviewed").reason == "reviewed"

    with pytest.raises(ValueError, match="execution_max_concurrent"):
        Settings(
            root=tmp_path,
            index_path=tmp_path / "index.db",
            execution_max_concurrent=0,
        )
    with pytest.raises(ValueError, match="wait"):
        RunProcessRequest("git", wait="no")  # type: ignore[arg-type]


def test_registry_rejects_capacity_cancels_and_hides_output(tmp_path: Path) -> None:
    limiter = ProjectExecutionLimiter(tmp_path, max_concurrent=1)
    registry = ProcessRegistry(limiter)
    initial = _result("first")
    started = threading.Event()
    released = threading.Event()

    def operation(control):  # type: ignore[no-untyped-def]
        control.register_terminator(released.set)
        control.publish(replace(initial, state=ExecutionState.RUNNING))
        started.set()
        assert released.wait(timeout=5)
        return replace(
            initial,
            state=ExecutionState.CANCELLED,
            stdout="redacted output",
            finished_at=datetime.now(UTC).isoformat(),
        )

    registry.reserve("first", initial)
    registry.submit("first", operation, lambda error: initial, propagate_errors=False)
    assert started.wait(timeout=5)
    assert registry.get("first").state is ExecutionState.RUNNING
    with pytest.raises(ExecutionConcurrencyLimitError):
        registry.reserve("second", _result("second"))

    cancelled = registry.terminate("first", reason="test")
    assert cancelled.state is ExecutionState.CANCELLED
    assert registry.terminate("first").state is ExecutionState.CANCELLED
    assert registry.get("first", include_output=False).stdout == ""

    registry.reserve("second", _result("second"))
    registry.discard("second")
    registry.shutdown()


def test_project_limiter_is_cross_process_and_crash_safe(tmp_path: Path) -> None:
    limiter = ProjectExecutionLimiter(tmp_path, max_concurrent=1)
    lease = limiter.try_acquire()
    assert lease is not None
    probe = (
        "from pathlib import Path;"
        "from code_harness.infrastructure.execution.runners.concurrency "
        "import ProjectExecutionLimiter;"
        f"item=ProjectExecutionLimiter(Path({str(tmp_path)!r}),max_concurrent=1).try_acquire();"
        "raise SystemExit(0 if item is None else 1)"
    )
    assert subprocess.run([sys.executable, "-c", probe], check=False).returncode == 0
    lease.release()

    crash = (
        "import os;"
        "from pathlib import Path;"
        "from code_harness.infrastructure.execution.runners.concurrency "
        "import ProjectExecutionLimiter;"
        f"item=ProjectExecutionLimiter(Path({str(tmp_path)!r}),max_concurrent=1).try_acquire();"
        "os._exit(0 if item is not None else 2)"
    )
    assert subprocess.run([sys.executable, "-c", crash], check=False).returncode == 0
    recovered = limiter.try_acquire()
    assert recovered is not None
    recovered.release()


def test_registry_lru_evicts_only_terminal_results(tmp_path: Path) -> None:
    registry = ProcessRegistry(
        ProjectExecutionLimiter(tmp_path, max_concurrent=1),
        completed_result_limit=1,
    )
    for execution_id in ("one", "two"):
        initial = _result(execution_id)
        registry.reserve(execution_id, initial)
        registry.submit(
            execution_id,
            lambda _control, item=execution_id: _result(
                item,
                state=ExecutionState.COMPLETED,
                stdout=item,
            ),
            lambda error, fallback=initial: fallback,
            propagate_errors=False,
        )
        assert registry.wait(execution_id).state is ExecutionState.COMPLETED

    with pytest.raises(ExecutionNotFoundError):
        registry.get("one")
    assert registry.get("two").stdout == "two"


def test_registry_propagates_sync_error_and_shutdown_is_idempotent(tmp_path: Path) -> None:
    registry = ProcessRegistry(ProjectExecutionLimiter(tmp_path, max_concurrent=1))
    initial = _result("failure")

    def fail(_control):  # type: ignore[no-untyped-def]
        raise RuntimeError("runner failed")

    registry.reserve("failure", initial)
    registry.submit(
        "failure",
        fail,
        lambda error: replace(initial, state=ExecutionState.FAILED, stderr=str(error)),
        propagate_errors=True,
    )
    with pytest.raises(RuntimeError, match="runner failed"):
        registry.wait("failure")
    assert registry.get("failure").state is ExecutionState.FAILED

    registry.shutdown()
    registry.shutdown()
    with pytest.raises(RuntimeError, match="shutting down"):
        registry.reserve("late", _result("late"))


def test_run_process_async_transitions_and_terminate_tool(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.db")
    store.initialize()
    registry = ProcessRegistry(ProjectExecutionLimiter(tmp_path, max_concurrent=1))
    runner = _ControlledRunner()
    redactor = SensitiveDataRedactor(tmp_path)
    tool = RunProcessTool(
        inspect_process=_Inspect(_inspection()),  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        project_id="project-e4",
        store=store,
        approvals=store,
        redactor=redactor,
        registry=registry,
    )

    accepted = tool.execute(RunProcessRequest("git", wait=False)).data
    assert accepted.state is ExecutionState.STARTING
    assert runner.started.wait(timeout=5)
    running = GetExecutionTool(registry).execute(GetExecutionRequest(accepted.execution_id)).data
    assert running.state is ExecutionState.RUNNING

    cancelled = (
        TerminateExecutionTool(
            registry=registry,
            store=store,
            redactor=redactor,
        )
        .execute(TerminateExecutionRequest(accepted.execution_id, "user request"))
        .data
    )
    assert cancelled.state is ExecutionState.CANCELLED
    with sqlite3.connect(store.path) as connection:
        state, output = connection.execute(
            "SELECT state, error_message FROM executions WHERE execution_id = ?",
            (accepted.execution_id,),
        ).fetchone()
        events = {
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM execution_events WHERE execution_id = ?",
                (accepted.execution_id,),
            )
        }
    assert state == ExecutionState.CANCELLED.value
    assert output == "Execution was cancelled."
    assert {
        "execution_running",
        "execution_cancellation_requested",
        "execution_cancelled",
    } <= events
    assert b"token=[REDACTED]" not in store.path.read_bytes()


def test_capacity_rejection_does_not_consume_approval(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.db")
    store.initialize()
    limiter = ProjectExecutionLimiter(tmp_path, max_concurrent=1)
    registry = ProcessRegistry(limiter)
    registry.reserve("occupied", _result("occupied"))

    approval = store.request_approval(
        project_id="project-e4",
        digest="digest-e4",
        command_kind=CommandKind.PROCESS.value,
        command_summary="git status",
        required_capabilities=(),
        backend="host_supervised",
        policy_name="deterministic_v1",
        policy_version="1",
        ruleset_hash="rules-e4",
        ttl_seconds=60,
    )
    store.decide_approval(
        "project-e4",
        approval.approval_id,
        state=ApprovalState.APPROVED,
    )
    tool = RunProcessTool(
        inspect_process=_Inspect(_inspection(approval_required=True)),  # type: ignore[arg-type]
        runner=_ControlledRunner(),  # type: ignore[arg-type]
        project_id="project-e4",
        store=store,
        approvals=store,
        redactor=SensitiveDataRedactor(tmp_path),
        registry=registry,
    )

    with pytest.raises(ExecutionConcurrencyLimitError):
        tool.execute(
            RunProcessRequest(
                "git",
                approval_id=approval.approval_id,
                wait=False,
            )
        )
    assert store.get_approval("project-e4", approval.approval_id).state is ApprovalState.APPROVED
    registry.discard("occupied")


def test_powershell_async_and_sync_share_registry_lifecycle(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.db")
    store.initialize()
    registry = ProcessRegistry(ProjectExecutionLimiter(tmp_path, max_concurrent=1))
    redactor = SensitiveDataRedactor(tmp_path)

    def approved_id() -> str:
        approval = store.request_approval(
            project_id="project-e4",
            digest="digest-e4",
            command_kind=CommandKind.POWERSHELL.value,
            command_summary="pwsh script_sha256=script-e4",
            required_capabilities=(),
            backend="host_supervised",
            policy_name="deterministic_v1",
            policy_version="1",
            ruleset_hash="rules-e4",
            ttl_seconds=60,
        )
        store.decide_approval(
            "project-e4",
            approval.approval_id,
            state=ApprovalState.APPROVED,
        )
        return approval.approval_id

    completed_runner = _ControlledRunner()
    completed_runner.release.set()
    sync_tool = RunPowerShellTool(
        inspect_powershell=_Inspect(_powershell_inspection()),  # type: ignore[arg-type]
        runner=completed_runner,  # type: ignore[arg-type]
        powershell_enabled=True,
        project_id="project-e4",
        store=store,
        approvals=store,
        redactor=redactor,
        registry=registry,
    )
    completed = sync_tool.execute(
        RunPowerShellRequest(
            "Write-Output 'done'",
            approval_id=approved_id(),
        )
    ).data
    assert completed.state is ExecutionState.COMPLETED

    async_runner = _ControlledRunner()
    async_tool = RunPowerShellTool(
        inspect_powershell=_Inspect(_powershell_inspection()),  # type: ignore[arg-type]
        runner=async_runner,  # type: ignore[arg-type]
        powershell_enabled=True,
        project_id="project-e4",
        store=store,
        approvals=store,
        redactor=redactor,
        registry=registry,
    )
    accepted = async_tool.execute(
        RunPowerShellRequest(
            "Start-Sleep 30",
            approval_id=approved_id(),
            wait=False,
        )
    ).data
    assert async_runner.started.wait(timeout=5)
    cancelled = (
        TerminateExecutionTool(
            registry=registry,
            store=store,
            redactor=redactor,
        )
        .execute(TerminateExecutionRequest(accepted.execution_id))
        .data
    )
    assert cancelled.state is ExecutionState.CANCELLED


def test_recovery_marks_only_unlocked_active_slot_interrupted(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.db")
    store.initialize()
    limiter = ProjectExecutionLimiter(tmp_path, max_concurrent=1)
    store.authorize_and_start(_audit_start("stale"), approval_id=None)
    live_lease = limiter.try_acquire_slot(0)
    assert live_lease is not None

    _recover_interrupted(store, limiter, "project-e4")
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT state FROM executions WHERE execution_id = 'stale'"
        ).fetchone() == (ExecutionState.STARTING.value,)

    live_lease.release()
    _recover_interrupted(store, limiter, "project-e4")
    with sqlite3.connect(store.path) as connection:
        state, error_code = connection.execute(
            "SELECT state, error_code FROM executions WHERE execution_id = 'stale'"
        ).fetchone()
    assert state == ExecutionState.FAILED.value
    assert error_code == "execution_interrupted"
