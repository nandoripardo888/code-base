import os
import time
from pathlib import Path

import pytest

from code_harness.domain.enums import ExecutionState
from code_harness.domain.errors import ExecutionApprovalRequiredError
from code_harness.interfaces.python_api import CodeHarness

pytestmark = pytest.mark.windows_e4


def _is_elevated() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return True


def _pid_running(pid: int) -> bool:
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 258
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows")
def test_async_powershell_cancellation_kills_child_tree_and_cleans_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("CODE_HARNESS_RUN_WINDOWS_E4") != "1":
        pytest.skip("set CODE_HARNESS_RUN_WINDOWS_E4=1 on the dedicated Windows runner")
    if _is_elevated():
        pytest.skip("host_supervised integration requires a non-elevated session")

    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_POWERSHELL", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_HOME", str(tmp_path / "state"))
    pid_file = tmp_path / "child.pid"
    script = (
        "$child = Start-Process pwsh "
        "-ArgumentList '-NoLogo','-NoProfile','-NonInteractive','-Command',"
        "'Start-Sleep -Seconds 60' -PassThru; "
        f"Set-Content -LiteralPath '{pid_file}' -Value $child.Id; "
        "Start-Sleep -Seconds 60"
    )

    with CodeHarness.open(tmp_path) as harness:
        with pytest.raises(ExecutionApprovalRequiredError) as required:
            harness.run_powershell(script)
        approval_id = str(required.value.details["approval_id"])
        harness.approve_execution(approval_id, reason="E4 cancellation integration")
        accepted = harness.run_powershell(
            script,
            approval_id=approval_id,
            wait=False,
        ).data
        deadline = time.monotonic() + 10
        current = harness.get_execution(accepted.execution_id).data
        while current.state is ExecutionState.STARTING and time.monotonic() < deadline:
            time.sleep(0.05)
            current = harness.get_execution(accepted.execution_id).data
        assert current.state is ExecutionState.RUNNING
        while not pid_file.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        child_pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert _pid_running(child_pid)

        cancelled = harness.terminate_execution(
            accepted.execution_id,
            reason="E4 integration",
        ).data
        assert cancelled.state is ExecutionState.CANCELLED
        assert not _pid_running(child_pid)
        assert not tuple((tmp_path / "state").rglob("ps-*"))
