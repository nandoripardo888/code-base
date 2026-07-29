"""Synchronous E1 runner. Windows implementation is deliberately host-supervised."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from code_harness.domain.errors import (
    ExecutionElevatedSessionError,
    ExecutionNotSupportedError,
)
from code_harness.domain.models.execution import NormalizedProcessCommand, ProcessRunOutcome
from code_harness.domain.protocols.execution_runtime import ExecutionTaskControl
from code_harness.infrastructure.execution.runners.executable_resolver import (
    HostExecutableResolver,
)


class SupervisedProcessRunner:
    def __init__(self, *, execution_home: str, max_processes: int, allow_elevated: bool) -> None:
        self._execution_home = Path(execution_home)
        self._max_processes = max_processes
        self._allow_elevated = allow_elevated

    def run(self, command: NormalizedProcessCommand) -> ProcessRunOutcome:
        return self._run(command)

    def run_controlled(
        self,
        command: NormalizedProcessCommand,
        *,
        control: ExecutionTaskControl,
        on_started: Callable[[], None],
    ) -> ProcessRunOutcome:
        return self._run(command, control=control, on_started=on_started)

    def _run(
        self,
        command: NormalizedProcessCommand,
        *,
        control: ExecutionTaskControl | None = None,
        on_started: Callable[[], None] | None = None,
    ) -> ProcessRunOutcome:
        if control is not None and control.cancellation_requested:
            return _cancelled_outcome()
        if os.name != "nt":
            raise ExecutionNotSupportedError(
                "host_supervised execution currently requires Windows."
            )
        if _is_elevated() and not self._allow_elevated:
            raise ExecutionElevatedSessionError()
        from code_harness.infrastructure.execution.runners.windows_process import (
            run_windows_process,
        )

        executable = command.resolved_executable or self._resolve_executable(
            command.executable, command.cwd
        )
        return run_windows_process(
            executable=executable,
            args=command.args,
            cwd=command.cwd,
            timeout_seconds=command.timeout_seconds,
            max_output_bytes=command.max_output_bytes,
            execution_home=self._execution_home,
            max_processes=self._max_processes,
            control=control,
            on_started=on_started,
        )

    @staticmethod
    def _resolve_executable(executable: str, cwd: str) -> str:
        return HostExecutableResolver().resolve(executable, cwd=cwd)


def _is_elevated() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):  # pragma: no cover - unavailable Windows API
        return True


def _cancelled_outcome() -> ProcessRunOutcome:
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
