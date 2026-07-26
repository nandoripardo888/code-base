import os
import runpy
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from code_harness.application.dto.execution_requests import RunProcessRequest
from code_harness.application.execution import (
    ApprovalAdminTool,
    DeterministicPolicyEngine,
    InspectProcessTool,
    RunProcessTool,
)
from code_harness.domain.enums import ApprovalState, ExecutionState
from code_harness.domain.errors import (
    ExecutionApprovalConsumedError,
    ExecutionApprovalDeniedError,
    ExecutionApprovalExpiredError,
    ExecutionApprovalInvalidError,
    ExecutionApprovalNotFoundError,
    ExecutionApprovalRequiredError,
    ExecutionElevatedSessionError,
    ExecutionNotSupportedError,
    ExecutionPolicyDeniedError,
    ExecutionStoreUnavailableError,
    InvalidExecutionRequestError,
    ProcessStartError,
)
from code_harness.domain.models.execution import (
    ExecutionRuntimeConfig,
    NormalizedProcessCommand,
    ProcessRunOutcome,
)
from code_harness.infrastructure.diagnostics.local_diagnostic_provider import (
    LocalDiagnosticProvider,
)
from code_harness.infrastructure.embeddings.fake_embedding_provider import FakeEmbeddingProvider
from code_harness.infrastructure.execution.persistence import SQLiteExecutionStore
from code_harness.infrastructure.execution.persistence import migrations as execution_migrations
from code_harness.infrastructure.execution.redaction import SensitiveDataRedactor
from code_harness.infrastructure.execution.runners import (
    HostExecutableResolver,
    SupervisedProcessRunner,
)
from code_harness.infrastructure.filesystem import PathGuard


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []
        self.error: Exception | None = None

    def run(self, command):  # type: ignore[no-untyped-def]
        self.calls.append(command)
        if self.error is not None:
            raise self.error
        return ProcessRunOutcome(
            exit_code=0,
            stdout="Authorization: Bearer top-secret-token\n",
            stderr="",
            stdout_bytes=40,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            elapsed_ms=8,
        )


def _e2(
    root: Path,
    *,
    require_approval: bool = True,
) -> tuple[RunProcessTool, ApprovalAdminTool, FakeRunner, Path]:
    config = ExecutionRuntimeConfig(
        backend="host_supervised",
        require_approval=require_approval,
        default_timeout_seconds=60.0,
        max_timeout_seconds=1800.0,
        max_output_bytes=200_000,
        max_processes=32,
        allow_elevated=False,
        execution_home=str(root / "state"),
        project_id="project-e2",
        approval_ttl_seconds=600,
    )
    inspection = InspectProcessTool(
        paths=PathGuard(root),
        policy=DeterministicPolicyEngine(config),
        config=config,
    )
    path = root / "state" / "execution.db"
    store = SQLiteExecutionStore(path)
    store.initialize()
    redactor = SensitiveDataRedactor()
    runner = FakeRunner()
    tool = RunProcessTool(
        inspect_process=inspection,
        runner=runner,
        project_id=config.project_id,
        store=store,
        approvals=store,
        redactor=redactor,
        approval_ttl_seconds=config.approval_ttl_seconds,
        require_approval=config.require_approval,
    )
    admin = ApprovalAdminTool(
        project_id=config.project_id,
        store=store,
        redact=redactor,
    )
    return tool, admin, runner, path


def test_approval_lifecycle_is_single_use_and_audited(tmp_path: Path) -> None:
    tool, admin, runner, database = _e2(tmp_path)
    request = RunProcessRequest("python", ("-c", "print('ok')"))

    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(request)

    approval_id = str(required.value.details["approval_id"])
    pending = admin.get(approval_id).data
    assert pending.state is ApprovalState.PENDING
    assert admin.approve(approval_id, reason="Reviewed locally").data.state is (
        ApprovalState.APPROVED
    )

    result = tool.execute(
        RunProcessRequest(
            "python",
            ("-c", "print('ok')"),
            approval_id=approval_id,
        )
    ).data

    assert result.state is ExecutionState.COMPLETED
    assert result.stdout == "Authorization: [REDACTED]\n"
    assert admin.get(approval_id).data.state is ApprovalState.CONSUMED
    assert len(runner.calls) == 1

    with pytest.raises(ExecutionApprovalConsumedError):
        tool.execute(
            RunProcessRequest(
                "python",
                ("-c", "print('ok')"),
                approval_id=approval_id,
            )
        )
    with pytest.raises(ExecutionApprovalConsumedError):
        admin.approve(approval_id)

    raw_database = database.read_bytes()
    assert b"top-secret-token" not in raw_database
    with sqlite3.connect(database) as connection:
        states = connection.execute("SELECT state FROM executions").fetchall()
        events = {row[0] for row in connection.execute("SELECT event_type FROM execution_events")}
    assert states == [(ExecutionState.COMPLETED.value,)]
    assert {
        "approval_requested",
        "approval_approved",
        "approval_consumed",
        "execution_started",
        "execution_completed",
    } <= events


def test_require_approval_wraps_an_otherwise_autoallowed_command(tmp_path: Path) -> None:
    tool, admin, runner, _database = _e2(tmp_path)

    with pytest.raises(ExecutionApprovalRequiredError):
        tool.execute(RunProcessRequest("git", ("status", "--short")))

    assert admin.list(state=ApprovalState.PENDING).data
    assert runner.calls == []


def test_legacy_tool_rejects_approval_id_without_store(tmp_path: Path) -> None:
    config = ExecutionRuntimeConfig(
        backend="host_supervised",
        require_approval=False,
        default_timeout_seconds=60,
        max_timeout_seconds=1_800,
        max_output_bytes=200_000,
        max_processes=32,
        allow_elevated=False,
        execution_home=str(tmp_path / "state"),
        project_id="legacy",
    )
    inspection = InspectProcessTool(
        paths=PathGuard(tmp_path),
        policy=DeterministicPolicyEngine(config),
        config=config,
    )
    runner = FakeRunner()
    tool = RunProcessTool(inspect_process=inspection, runner=runner)

    with pytest.raises(ExecutionApprovalRequiredError):
        tool.execute(
            RunProcessRequest(
                "python",
                ("-c", "print('legacy')"),
                approval_id="approval-without-store",
            )
        )

    assert runner.calls == []


def test_autoallowed_command_is_audited_without_approval_when_configured(
    tmp_path: Path,
) -> None:
    tool, admin, runner, database = _e2(tmp_path, require_approval=False)

    result = tool.execute(RunProcessRequest("git", ("status", "--short"))).data

    assert result.state is ExecutionState.COMPLETED
    assert admin.list().data == ()
    assert len(runner.calls) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT approval_id FROM executions").fetchone() == (None,)


def test_approval_is_bound_to_exact_digest(tmp_path: Path) -> None:
    tool, admin, runner, _database = _e2(tmp_path)

    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunProcessRequest("python", ("-c", "print('one')")))
    approval_id = str(required.value.details["approval_id"])
    admin.approve(approval_id)

    with pytest.raises(ExecutionApprovalInvalidError):
        tool.execute(
            RunProcessRequest(
                "python",
                ("-c", "print('two')"),
                approval_id=approval_id,
            )
        )

    assert runner.calls == []
    assert admin.get(approval_id).data.state is ApprovalState.APPROVED


def test_hard_deny_is_audited_without_creating_approval(tmp_path: Path) -> None:
    tool, admin, runner, database = _e2(tmp_path)

    with pytest.raises(ExecutionPolicyDeniedError):
        tool.execute(RunProcessRequest("git", ("reset", "--hard", "HEAD")))

    assert runner.calls == []
    assert admin.list().data == ()
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT state, error_code FROM executions").fetchone()
    assert row == (ExecutionState.BLOCKED.value, "execution_policy_denied")


def test_approval_admin_same_decision_is_idempotent(tmp_path: Path) -> None:
    tool, admin, _runner, _database = _e2(tmp_path)

    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunProcessRequest("python", ("-c", "print('review')")))
    approval_id = str(required.value.details["approval_id"])

    first = admin.deny(approval_id, reason="No").data
    second = admin.deny(approval_id, reason="Ignored").data

    assert first == second
    assert first.state is ApprovalState.DENIED
    assert admin.list(state=ApprovalState.DENIED).data == (first,)


def test_concurrent_consumption_runs_exactly_once(tmp_path: Path) -> None:
    tool, admin, runner, _database = _e2(tmp_path)
    args = ("-c", "print('once')")

    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunProcessRequest("python", args))
    approval_id = str(required.value.details["approval_id"])
    admin.approve(approval_id)

    def attempt() -> str:
        try:
            return tool.execute(
                RunProcessRequest("python", args, approval_id=approval_id)
            ).data.state.value
        except ExecutionApprovalConsumedError:
            return "consumed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _index: attempt(), range(2)))

    assert sorted(outcomes) == ["completed", "consumed"]
    assert len(runner.calls) == 1


def test_expired_approval_is_marked_and_can_be_requested_again(tmp_path: Path) -> None:
    now = [datetime(2026, 7, 25, tzinfo=UTC)]
    store = SQLiteExecutionStore(tmp_path / "execution.db", clock=lambda: now[0])
    store.initialize()
    fields = {
        "project_id": "project",
        "digest": "digest",
        "command_kind": "process",
        "command_summary": "python -c review",
        "required_capabilities": ("process_spawn",),
        "backend": "host_supervised",
        "policy_name": "deterministic_v1",
        "policy_version": "1",
        "ruleset_hash": "rules",
        "ttl_seconds": 10,
    }

    first = store.request_approval(**fields)
    now[0] += timedelta(seconds=11)

    assert store.get_approval("project", first.approval_id).state is ApprovalState.EXPIRED
    second = store.request_approval(**fields)
    assert second.state is ApprovalState.PENDING
    assert second.approval_id != first.approval_id


def test_expiration_is_rechecked_atomically_before_execution(tmp_path: Path) -> None:
    tool, admin, runner, database = _e2(tmp_path)
    args = ("-c", "print('expires')")
    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunProcessRequest("python", args))
    approval_id = str(required.value.details["approval_id"])
    admin.approve(approval_id)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE approval_requests SET expires_at = ? WHERE approval_id = ?",
            ("2000-01-01T00:00:00+00:00", approval_id),
        )

    with pytest.raises(ExecutionApprovalExpiredError):
        tool.execute(RunProcessRequest("python", args, approval_id=approval_id))

    assert runner.calls == []


def test_runner_failure_is_sanitized_and_audited(tmp_path: Path) -> None:
    tool, admin, runner, database = _e2(tmp_path)
    args = ("-c", "print('failure')")
    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunProcessRequest("python", args))
    approval_id = str(required.value.details["approval_id"])
    admin.approve(approval_id)
    runner.error = ProcessStartError("token=super-secret-value")

    with pytest.raises(ProcessStartError):
        tool.execute(RunProcessRequest("python", args, approval_id=approval_id))

    with sqlite3.connect(database) as connection:
        state, message = connection.execute(
            "SELECT state, error_message FROM executions"
        ).fetchone()
    assert state == ExecutionState.FAILED.value
    assert message == "token=[REDACTED]"
    assert b"super-secret-value" not in database.read_bytes()


def test_doctor_reports_execution_audit_store(tmp_path: Path) -> None:
    database = tmp_path / "state" / "execution.db"
    SQLiteExecutionStore(database).initialize()
    provider = LocalDiagnosticProvider(
        tmp_path,
        tmp_path / "index.db",
        "rg",
        execution_enabled=True,
        execution_home=database.parent,
        execution_store_path=database,
    )

    checks = {item.name: item for item in provider.run().checks}

    assert checks["execution_audit_store"].status.value == "pass"
    assert "schema=1/1" in checks["execution_audit_store"].message


def test_denial_cooldown_and_approval_admin_guards(tmp_path: Path) -> None:
    tool, admin, runner, _database = _e2(tmp_path)
    args = ("-c", "print('denied')")
    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunProcessRequest("python", args))
    approval_id = str(required.value.details["approval_id"])
    with pytest.raises(ExecutionApprovalInvalidError):
        tool.execute(RunProcessRequest("python", args, approval_id=approval_id))
    admin.deny(approval_id)

    with pytest.raises(ExecutionApprovalDeniedError):
        tool.execute(RunProcessRequest("python", args))
    with pytest.raises(ExecutionApprovalDeniedError):
        tool.execute(RunProcessRequest("python", args, approval_id=approval_id))
    with pytest.raises(ExecutionApprovalNotFoundError):
        tool.execute(RunProcessRequest("python", args, approval_id="missing"))
    with pytest.raises(ExecutionApprovalInvalidError):
        admin.approve(approval_id)
    with pytest.raises(ExecutionApprovalNotFoundError):
        admin.get("missing")
    with pytest.raises(InvalidExecutionRequestError):
        admin.list(limit=0)
    with pytest.raises(InvalidExecutionRequestError):
        admin.approve("")
    with pytest.raises(InvalidExecutionRequestError):
        admin.approve(approval_id, reason="x" * 4_001)
    assert runner.calls == []


def test_pending_expired_and_consumed_decision_guards(tmp_path: Path) -> None:
    now = [datetime(2026, 7, 25, tzinfo=UTC)]
    store = SQLiteExecutionStore(tmp_path / "execution.db", clock=lambda: now[0])
    store.initialize()
    redactor = SensitiveDataRedactor()
    admin = ApprovalAdminTool(project_id="project", store=store, redact=redactor)

    def request(digest: str):
        return store.request_approval(
            project_id="project",
            digest=digest,
            command_kind="process",
            command_summary="python review",
            required_capabilities=("process_spawn",),
            backend="host_supervised",
            policy_name="deterministic_v1",
            policy_version="1",
            ruleset_hash="rules",
            ttl_seconds=10,
        )

    pending = request("pending")
    with pytest.raises(ExecutionApprovalInvalidError):
        store.decide_approval(
            "project",
            pending.approval_id,
            state=ApprovalState.PENDING,
        )

    expired = request("expired")
    now[0] += timedelta(seconds=11)
    with pytest.raises(ExecutionApprovalExpiredError):
        admin.approve(expired.approval_id)
    with pytest.raises(ExecutionApprovalNotFoundError):
        admin.approve("missing")


def test_migration_and_missing_completion_fail_safely(tmp_path: Path) -> None:
    database = tmp_path / "execution.db"
    store = SQLiteExecutionStore(database)
    store.initialize()
    store.initialize()

    with pytest.raises(ExecutionStoreUnavailableError):
        store.finish_execution(
            "missing",
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
        )

    future = tmp_path / "future.db"
    with sqlite3.connect(future) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(ExecutionStoreUnavailableError):
        SQLiteExecutionStore(future).initialize()


def test_execution_store_maps_sqlite_failures_and_rolls_back_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "invalid.db"
    invalid.write_text("not sqlite", encoding="utf-8")
    store = SQLiteExecutionStore(invalid)
    with pytest.raises(ExecutionStoreUnavailableError):
        store.list_approvals("project")
    with pytest.raises(ExecutionStoreUnavailableError):
        store.get_approval("project", "missing")
    with pytest.raises(ExecutionStoreUnavailableError):
        store.decide_approval(
            "project",
            "missing",
            state=ApprovalState.APPROVED,
        )

    monkeypatch.setattr(
        execution_migrations,
        "MIGRATIONS",
        ((1, "broken", ("THIS IS NOT SQL",)),),
    )
    with pytest.raises(ExecutionStoreUnavailableError):
        execution_migrations.apply_execution_migrations(tmp_path / "broken.db")


def test_doctor_reports_missing_and_invalid_execution_store(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    provider = LocalDiagnosticProvider(
        tmp_path,
        tmp_path / "index.db",
        "rg",
        execution_enabled=True,
        execution_store_path=missing,
    )
    checks = {item.name: item for item in provider.run().checks}
    assert checks["execution_audit_store"].status.value == "warning"

    invalid = tmp_path / "invalid.db"
    invalid.write_text("not sqlite", encoding="utf-8")
    provider = LocalDiagnosticProvider(
        tmp_path,
        tmp_path / "index.db",
        "rg",
        execution_enabled=True,
        execution_store_path=invalid,
    )
    checks = {item.name: item for item in provider.run().checks}
    assert checks["execution_audit_store"].status.value == "fail"


def test_deep_doctor_keeps_semantic_and_execution_checks_healthy(tmp_path: Path) -> None:
    database = tmp_path / "execution.db"
    SQLiteExecutionStore(database).initialize()
    embeddings = FakeEmbeddingProvider()
    provider = LocalDiagnosticProvider(
        tmp_path,
        tmp_path / "index.db",
        "rg",
        embedding_provider=embeddings,
        semantic_enabled=True,
        execution_enabled=True,
        execution_store_path=database,
    )

    checks = {item.name: item for item in provider.run(deep=True).checks}

    assert checks["semantic"].status.value == "pass"
    assert checks["execution_audit_store"].status.value == "pass"
    assert embeddings.query_calls
    assert embeddings.document_calls

    class InvalidEmbeddingProvider(FakeEmbeddingProvider):
        def embed_query(self, text: str):  # type: ignore[no-untyped-def]
            return (1.0,)

    provider = LocalDiagnosticProvider(
        tmp_path,
        tmp_path / "index.db",
        "rg",
        embedding_provider=InvalidEmbeddingProvider(),
        semantic_enabled=True,
    )
    checks = {item.name: item for item in provider.run(deep=True).checks}
    assert checks["semantic"].status.value == "warning"


def test_executable_resolver_rejects_paths_and_missing_program(tmp_path: Path) -> None:
    resolver = HostExecutableResolver()
    with pytest.raises(ProcessStartError):
        resolver.resolve(str(Path(sys.executable).resolve()), cwd=str(tmp_path))
    with pytest.raises(ProcessStartError):
        resolver.resolve("definitely-not-a-real-program-e2", cwd=str(tmp_path))

    redactor = SensitiveDataRedactor(tmp_path / "project")
    summary = redactor.summarize_command("python", (), cwd=str(tmp_path / "outside"))
    assert "outside" in summary


def test_supervised_runner_platform_and_elevation_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = SupervisedProcessRunner(
        execution_home=str(tmp_path / "runtime"),
        max_processes=1,
        allow_elevated=False,
    )
    command = NormalizedProcessCommand(
        executable="python",
        args=("--version",),
        cwd=str(tmp_path),
        timeout_seconds=1,
        max_output_bytes=1_000,
        requested_capabilities=(),
        reason=None,
        resolved_executable=str(Path(sys.executable).resolve()),
    )
    if os.name != "nt":
        with pytest.raises(ExecutionNotSupportedError):
            runner.run(command)
        return

    import code_harness.infrastructure.execution.runners.supervised_process_runner as module

    monkeypatch.setattr(module, "_is_elevated", lambda: True)
    assert module._is_elevated() is True
    with pytest.raises(ExecutionElevatedSessionError):
        runner.run(command)

    import code_harness.infrastructure.execution.runners.windows_process as windows_process

    expected = ProcessRunOutcome(
        exit_code=0,
        stdout="",
        stderr="",
        stdout_bytes=0,
        stderr_bytes=0,
        stdout_truncated=False,
        stderr_truncated=False,
        timed_out=False,
        elapsed_ms=1,
    )
    monkeypatch.setattr(module, "_is_elevated", lambda: False)
    monkeypatch.setattr(windows_process, "run_windows_process", lambda **_kwargs: expected)
    assert runner.run(command) is expected
    monkeypatch.setattr(
        HostExecutableResolver,
        "resolve",
        lambda self, executable, *, cwd: str(Path(sys.executable).resolve()),
    )
    command = NormalizedProcessCommand(
        executable="python",
        args=("--version",),
        cwd=str(tmp_path),
        timeout_seconds=1,
        max_output_bytes=1_000,
        requested_capabilities=(),
        reason=None,
    )
    assert runner.run(command) is expected


def test_module_entrypoint_delegates_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    import code_harness.interfaces.cli.main as cli_main

    calls: list[bool] = []
    monkeypatch.setattr(cli_main, "main", lambda: calls.append(True))

    runpy.run_module("code_harness.__main__", run_name="not_main")
    runpy.run_module("code_harness.__main__", run_name="__main__")

    assert calls == [True]
