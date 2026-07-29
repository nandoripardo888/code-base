import sqlite3
import sys
from pathlib import Path

import pytest

from code_harness.application.dto.execution_requests import RunPowerShellRequest
from code_harness.application.execution import (
    ApprovalAdminTool,
    DeterministicPolicyEngine,
    InspectPowerShellTool,
    RunPowerShellTool,
)
from code_harness.bootstrap.settings import Settings
from code_harness.domain.enums import ApprovalState, CommandKind, ExecutionState
from code_harness.domain.errors import (
    ExecutionApprovalInvalidError,
    ExecutionApprovalRequiredError,
    ExecutionPolicyDeniedError,
    PowerShellExecutionDisabledError,
    PowerShellUnavailableError,
    ProcessStartError,
)
from code_harness.domain.models.execution import (
    ExecutionRuntimeConfig,
    NormalizedPowerShellCommand,
    PowerShellAstAnalysis,
    ProcessRunOutcome,
)
from code_harness.infrastructure.execution.persistence import SQLiteExecutionStore
from code_harness.infrastructure.execution.redaction import SensitiveDataRedactor
from code_harness.infrastructure.execution.runners import (
    PowerShell7ExecutableResolver,
    SupervisedPowerShellRunner,
)
from code_harness.infrastructure.filesystem import PathGuard


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyze(self, script: str, *, timeout_seconds: float) -> PowerShellAstAnalysis:
        self.calls.append(script)
        features = ()
        lowered = script.casefold()
        if "invoke-expression" in lowered:
            features = ("invoke_expression",)
        return PowerShellAstAnalysis((), (script,), features)


class FakeResolver:
    def __init__(self) -> None:
        self.path = str(Path(sys.executable).resolve())

    def resolve(self, executable: str) -> str:
        return self.path


class FakePowerShellRunner:
    def __init__(self) -> None:
        self.calls: list[NormalizedPowerShellCommand] = []
        self.error: Exception | None = None

    def run(self, command: NormalizedPowerShellCommand) -> ProcessRunOutcome:
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
            elapsed_ms=7,
        )


def _tools(
    root: Path,
    *,
    powershell_enabled: bool = True,
) -> tuple[RunPowerShellTool, ApprovalAdminTool, FakePowerShellRunner, Path]:
    config = ExecutionRuntimeConfig(
        backend="host_supervised",
        require_approval=False,
        default_timeout_seconds=60,
        max_timeout_seconds=1_800,
        max_output_bytes=200_000,
        max_processes=32,
        allow_elevated=False,
        execution_home=str(root / "state"),
        project_id="project-e3",
        powershell_enabled=powershell_enabled,
    )
    inspection = InspectPowerShellTool(
        paths=PathGuard(root),
        policy=DeterministicPolicyEngine(config),
        analyzer=FakeAnalyzer(),
        executable_resolver=FakeResolver(),
        config=config,
    )
    database = root / "state" / "execution.db"
    store = SQLiteExecutionStore(database)
    store.initialize()
    runner = FakePowerShellRunner()
    redactor = SensitiveDataRedactor(root)
    tool = RunPowerShellTool(
        inspect_powershell=inspection,
        runner=runner,
        powershell_enabled=powershell_enabled,
        project_id=config.project_id,
        store=store,
        approvals=store,
        redactor=redactor,
        approval_ttl_seconds=600,
    )
    admin = ApprovalAdminTool(project_id=config.project_id, store=store, redact=redactor)
    return tool, admin, runner, database


def test_powershell_execution_has_an_independent_default_off_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.for_root(tmp_path)
    assert not settings.execution_powershell_enabled

    with pytest.raises(ValueError, match="execution_powershell_enabled"):
        Settings(
            root=tmp_path,
            index_path=tmp_path / "index.db",
            execution_powershell_enabled=True,
        )

    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_POWERSHELL", "1")
    monkeypatch.setenv("CODE_HARNESS_POWERSHELL", str(Path(sys.executable).resolve()))
    enabled = Settings.for_root(tmp_path)
    assert enabled.execution_powershell_enabled
    assert enabled.execution_powershell_executable == str(Path(sys.executable).resolve())

    tool, _admin, runner, _database = _tools(tmp_path, powershell_enabled=False)
    with pytest.raises(PowerShellExecutionDisabledError):
        tool.execute(RunPowerShellRequest("Write-Output 'blocked'"))
    assert runner.calls == []


def test_powershell_approval_is_single_use_audited_and_redacted(tmp_path: Path) -> None:
    tool, admin, runner, database = _tools(tmp_path)
    script = "Write-Output 'script-body-sentinel'"

    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunPowerShellRequest(script))
    approval_id = str(required.value.details["approval_id"])
    approval = admin.get(approval_id).data
    assert approval.command_kind is CommandKind.POWERSHELL
    assert "script_sha256=" in approval.command_summary
    assert "script-body-sentinel" not in approval.command_summary

    admin.approve(approval_id, reason="Reviewed locally")
    result = tool.execute(RunPowerShellRequest(script, approval_id=approval_id)).data

    assert result.state is ExecutionState.COMPLETED
    assert result.stdout == "Authorization: [REDACTED]\n"
    assert result.inspection.resolved_executable == str(Path(sys.executable).resolve())
    assert admin.get(approval_id).data.state is ApprovalState.CONSUMED
    assert len(runner.calls) == 1
    assert runner.calls[0].script == script
    assert b"script-body-sentinel" not in database.read_bytes()
    assert b"top-secret-token" not in database.read_bytes()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT command_kind FROM executions").fetchone() == (
            "powershell",
        )


def test_changed_script_invalidates_approval_and_hard_deny_never_runs(tmp_path: Path) -> None:
    tool, admin, runner, _database = _tools(tmp_path)
    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunPowerShellRequest("Write-Output 'one'"))
    approval_id = str(required.value.details["approval_id"])
    admin.approve(approval_id)

    with pytest.raises(ExecutionApprovalInvalidError):
        tool.execute(RunPowerShellRequest("Write-Output 'two'", approval_id=approval_id))
    with pytest.raises(ExecutionPolicyDeniedError):
        tool.execute(RunPowerShellRequest("Invoke-Expression $command"))
    assert runner.calls == []


def test_runner_failure_is_sanitized_and_audited(tmp_path: Path) -> None:
    tool, admin, runner, database = _tools(tmp_path)
    script = "Write-Output 'failure'"
    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunPowerShellRequest(script))
    approval_id = str(required.value.details["approval_id"])
    admin.approve(approval_id)
    runner.error = ProcessStartError("token=super-secret-value")

    with pytest.raises(ProcessStartError):
        tool.execute(RunPowerShellRequest(script, approval_id=approval_id))

    with sqlite3.connect(database) as connection:
        state, code, message = connection.execute(
            "SELECT state, error_code, error_message FROM executions"
        ).fetchone()
    assert state == ExecutionState.FAILED.value
    assert code == "process_start_failed"
    assert message == "token=[REDACTED]"
    assert b"super-secret-value" not in database.read_bytes()


def test_approved_powershell_fails_closed_without_audit_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, admin, runner, _database = _tools(tmp_path)
    script = "Write-Output 'store-required'"
    with pytest.raises(ExecutionApprovalRequiredError) as required:
        tool.execute(RunPowerShellRequest(script))
    approval_id = str(required.value.details["approval_id"])
    admin.approve(approval_id)
    monkeypatch.setattr(tool, "_store", None)

    with pytest.raises(ExecutionApprovalRequiredError, match="storage is unavailable"):
        tool.execute(RunPowerShellRequest(script, approval_id=approval_id))
    assert runner.calls == []


def test_powershell_executable_resolver_accepts_only_existing_absolute_path(
    tmp_path: Path,
) -> None:
    resolver = PowerShell7ExecutableResolver()
    executable = str(Path(sys.executable).resolve())
    assert resolver.resolve(executable) == executable

    with pytest.raises(PowerShellUnavailableError):
        resolver.resolve(str((tmp_path / "missing-pwsh.exe").resolve()))


def test_supervised_powershell_runner_uses_fixed_arguments_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import code_harness.infrastructure.execution.runners.powershell_runner as module

    captured: list[object] = []

    class FakeProcessRunner:
        def run(self, command):  # type: ignore[no-untyped-def]
            captured.append(command)
            script_path = Path(command.args[-1])
            assert script_path.read_text(encoding="utf-8") == "Write-Output 'ok'"
            return ProcessRunOutcome(
                exit_code=0,
                stdout="ok\n",
                stderr="",
                stdout_bytes=3,
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
                timed_out=False,
                elapsed_ms=1,
            )

    secured: list[Path] = []

    def fake_private_directory(path: Path) -> None:
        secured.append(path)
        path.mkdir()

    monkeypatch.setattr(module, "create_private_directory", fake_private_directory)
    runner = SupervisedPowerShellRunner(
        execution_home=str(tmp_path),
        process_runner=FakeProcessRunner(),
    )
    executable = str(Path(sys.executable).resolve())
    result = runner.run(
        NormalizedPowerShellCommand(
            script="Write-Output 'ok'",
            cwd=str(tmp_path),
            timeout_seconds=5,
            max_output_bytes=1_000,
            requested_capabilities=(),
            reason=None,
            resolved_executable=executable,
        )
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    process = captured[0]
    assert process.args[:4] == ("-NoLogo", "-NoProfile", "-NonInteractive", "-File")
    assert process.resolved_executable == executable
    assert secured and not secured[0].exists()

    with pytest.raises(PowerShellUnavailableError):
        runner.run(
            NormalizedPowerShellCommand(
                script="Write-Output 'missing'",
                cwd=str(tmp_path),
                timeout_seconds=5,
                max_output_bytes=1_000,
                requested_capabilities=(),
                reason=None,
            )
        )
