"""Real E1 checks, intentionally opt-in and never run from an elevated session."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from code_harness.domain.enums import ExecutionState
from code_harness.interfaces.python_api import CodeHarness

pytestmark = pytest.mark.windows_e1


def _eligible() -> bool:
    if os.name != "nt" or os.environ.get("CODE_HARNESS_RUN_WINDOWS_E1") != "1":
        return False
    try:
        return not bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


@pytest.mark.skipif(not _eligible(), reason="requires opt-in non-administrator Windows E1 runner")
def test_run_process_runs_git_status_under_job_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is unavailable")
    subprocess.run((git, "init", "-q"), cwd=tmp_path, check=True)
    monkeypatch.setenv("CODE_HARNESS_EXECUTION", "1")
    monkeypatch.setenv("CODE_HARNESS_EXECUTION_ALLOW_ELEVATED", "0")

    with CodeHarness.open(tmp_path) as harness:
        result = harness.run_process("git", ("status", "--short"))

    assert result.data.state is ExecutionState.COMPLETED
    assert result.data.exit_code == 0
    assert result.data.stderr == ""


@pytest.mark.skipif(not _eligible(), reason="requires opt-in non-administrator Windows E1 runner")
def test_runner_timeout_is_reported_and_job_is_closed(tmp_path: Path) -> None:
    from code_harness.domain.models.execution import NormalizedProcessCommand
    from code_harness.infrastructure.execution.runners import SupervisedProcessRunner

    runner = SupervisedProcessRunner(
        execution_home=str(tmp_path / "runtime"), max_processes=4, allow_elevated=False
    )
    outcome = runner.run(
        NormalizedProcessCommand(
            executable="python",
            args=("-c", "import time; time.sleep(10)"),
            cwd=str(tmp_path),
            timeout_seconds=0.1,
            max_output_bytes=1_024,
            requested_capabilities=(),
            reason=None,
        )
    )

    assert outcome.timed_out
    assert outcome.exit_code is None
