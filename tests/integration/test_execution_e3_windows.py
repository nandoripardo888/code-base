import os
import subprocess
from pathlib import Path

import pytest

from code_harness.domain.enums import ExecutionState
from code_harness.domain.errors import ExecutionApprovalRequiredError
from code_harness.infrastructure.execution.windows.acl import create_private_directory
from code_harness.interfaces.python_api import CodeHarness

pytestmark = pytest.mark.windows_e3


def _is_elevated() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return True


@pytest.mark.skipif(os.name != "nt", reason="requires Windows")
def test_powershell_artifact_directory_has_a_protected_dacl(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    create_private_directory(artifact)

    completed = subprocess.run(
        ["icacls", str(artifact)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )

    assert "(I)" not in completed.stdout
    assert os.environ["USERNAME"].casefold() in completed.stdout.casefold()
    assert completed.stdout.count("(OI)(CI)(F)") == 2


@pytest.mark.skipif(os.name != "nt", reason="requires Windows")
def test_run_powershell_executes_approved_script_under_job_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("CODE_HARNESS_RUN_WINDOWS_E3") != "1":
        pytest.skip("set CODE_HARNESS_RUN_WINDOWS_E3=1 on the dedicated Windows runner")
    if _is_elevated():
        pytest.skip("host_supervised integration requires a non-elevated session")

    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_POWERSHELL", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_HOME", str(tmp_path / "state"))
    harness = CodeHarness.open(tmp_path)
    checks = {item.name: item for item in harness.doctor().data.checks}
    assert checks["execution_powershell"].status.value == "pass"
    script = "[Console]::Error.WriteLine('e3-err'); Write-Output 'e3-ok'"

    with pytest.raises(ExecutionApprovalRequiredError) as required:
        harness.run_powershell(script)
    approval_id = str(required.value.details["approval_id"])
    harness.approve_execution(approval_id, reason="E3 integration")
    result = harness.run_powershell(script, approval_id=approval_id).data

    assert result.state is ExecutionState.COMPLETED
    assert result.exit_code == 0
    assert result.stdout.strip() == "e3-ok"
    assert result.stderr.strip() == "e3-err"
    assert result.inspection.resolved_executable
    assert not tuple((tmp_path / "state").rglob("ps-*"))

    timeout_script = "Start-Sleep -Seconds 30"
    with pytest.raises(ExecutionApprovalRequiredError) as timeout_required:
        harness.run_powershell(timeout_script, timeout_seconds=0.2)
    timeout_approval = str(timeout_required.value.details["approval_id"])
    harness.approve_execution(timeout_approval, reason="E3 timeout integration")
    timed_out = harness.run_powershell(
        timeout_script,
        timeout_seconds=0.2,
        approval_id=timeout_approval,
    ).data
    assert timed_out.state is ExecutionState.TIMED_OUT
    assert timed_out.exit_code is None
    assert not tuple((tmp_path / "state").rglob("ps-*"))
