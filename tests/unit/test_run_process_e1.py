from pathlib import Path

import pytest

from code_harness.application.dto.execution_requests import RunProcessRequest
from code_harness.application.execution import (
    DeterministicPolicyEngine,
    InspectProcessTool,
    RunProcessTool,
)
from code_harness.domain.enums import ExecutionState
from code_harness.domain.errors import ExecutionApprovalRequiredError, ExecutionPolicyDeniedError
from code_harness.domain.models.execution import ExecutionRuntimeConfig, ProcessRunOutcome
from code_harness.infrastructure.filesystem import PathGuard


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, command):  # type: ignore[no-untyped-def]
        self.calls.append(command)
        return ProcessRunOutcome(
            exit_code=0,
            stdout="clean\n",
            stderr="",
            stdout_bytes=6,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            elapsed_ms=12,
        )


def _tool(root: Path) -> tuple[RunProcessTool, FakeRunner]:
    config = ExecutionRuntimeConfig(
        backend="host_supervised",
        require_approval=True,
        default_timeout_seconds=60.0,
        max_timeout_seconds=1800.0,
        max_output_bytes=200_000,
        max_processes=32,
        allow_elevated=False,
        execution_home=str(root / ".code-harness" / "execution"),
        project_id="project",
    )
    inspect = InspectProcessTool(
        paths=PathGuard(root), policy=DeterministicPolicyEngine(config), config=config
    )
    runner = FakeRunner()
    return RunProcessTool(inspect_process=inspect, runner=runner), runner


def test_run_process_executes_only_the_autoallow_result(tmp_path: Path) -> None:
    tool, runner = _tool(tmp_path)

    result = tool.execute(RunProcessRequest("git", ("status", "--short")))

    assert result.data.state is ExecutionState.COMPLETED
    assert result.data.stdout == "clean\n"
    assert result.data.inspection.executable == "git"
    assert len(runner.calls) == 1


def test_run_process_rejects_destructive_command_before_runner(tmp_path: Path) -> None:
    tool, runner = _tool(tmp_path)

    with pytest.raises(ExecutionPolicyDeniedError):
        tool.execute(RunProcessRequest("git", ("reset", "--hard", "HEAD")))

    assert runner.calls == []


def test_run_process_requires_e2_for_approval_commands(tmp_path: Path) -> None:
    tool, runner = _tool(tmp_path)

    with pytest.raises(ExecutionApprovalRequiredError):
        tool.execute(RunProcessRequest("pytest", ("tests",)))

    assert runner.calls == []
