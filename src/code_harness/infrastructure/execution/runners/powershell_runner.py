from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from code_harness.domain.errors import PowerShellUnavailableError, ProcessStartError
from code_harness.domain.models.execution import (
    NormalizedPowerShellCommand,
    NormalizedProcessCommand,
    ProcessRunOutcome,
)
from code_harness.domain.protocols.command_policy import ProcessRunner
from code_harness.domain.protocols.execution_runtime import ExecutionTaskControl
from code_harness.infrastructure.execution.windows.acl import create_private_directory


class SupervisedPowerShellRunner:
    """Run an approved script through a fixed PowerShell 7 command line."""

    def __init__(self, *, execution_home: str, process_runner: ProcessRunner) -> None:
        self._execution_home = Path(execution_home)
        self._process_runner = process_runner

    def run(self, command: NormalizedPowerShellCommand) -> ProcessRunOutcome:
        return self._run(command)

    def run_controlled(
        self,
        command: NormalizedPowerShellCommand,
        *,
        control: ExecutionTaskControl,
        on_started: Callable[[], None],
    ) -> ProcessRunOutcome:
        return self._run(command, control=control, on_started=on_started)

    def _run(
        self,
        command: NormalizedPowerShellCommand,
        *,
        control: ExecutionTaskControl | None = None,
        on_started: Callable[[], None] | None = None,
    ) -> ProcessRunOutcome:
        if control is not None and control.cancellation_requested:
            return ProcessRunOutcome(
                exit_code=None,
                stdout="",
                stderr="",
                stdout_bytes=0,
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
                timed_out=False,
                elapsed_ms=0,
                cancelled=True,
            )
        executable = command.resolved_executable
        if executable is None or not Path(executable).is_absolute():
            raise PowerShellUnavailableError(executable or "pwsh")

        self._execution_home.mkdir(parents=True, exist_ok=True)
        artifact_dir = self._execution_home / f"ps-{uuid4().hex}"
        create_private_directory(artifact_dir)
        script_path = artifact_dir / "script.ps1"
        try:
            with script_path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(command.script)
            process = NormalizedProcessCommand(
                executable=executable,
                args=(
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(script_path),
                ),
                cwd=command.cwd,
                timeout_seconds=command.timeout_seconds,
                max_output_bytes=command.max_output_bytes,
                requested_capabilities=command.requested_capabilities,
                reason=command.reason,
                resolved_executable=executable,
            )
            if control is None or on_started is None:
                return self._process_runner.run(process)
            return self._process_runner.run_controlled(
                process,
                control=control,
                on_started=on_started,
            )
        finally:
            try:
                shutil.rmtree(artifact_dir)
            except OSError as error:
                if artifact_dir.exists():
                    raise ProcessStartError(
                        "Could not remove the protected PowerShell artifact."
                    ) from error
