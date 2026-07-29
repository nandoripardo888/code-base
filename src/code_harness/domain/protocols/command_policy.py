from collections.abc import Callable
from typing import Literal, Protocol

from code_harness.domain.models.execution import (
    CommandInspection,
    NormalizedPowerShellCommand,
    NormalizedProcessCommand,
    PowerShellAstAnalysis,
    ProcessRunOutcome,
)
from code_harness.domain.protocols.execution_runtime import ExecutionTaskControl


class ProcessRunner(Protocol):
    def run(self, command: NormalizedProcessCommand) -> ProcessRunOutcome:
        """Run one already-authorized structured process."""
        ...

    def run_controlled(
        self,
        command: NormalizedProcessCommand,
        *,
        control: ExecutionTaskControl,
        on_started: Callable[[], None],
    ) -> ProcessRunOutcome: ...


class PowerShellRunner(Protocol):
    def run(self, command: NormalizedPowerShellCommand) -> ProcessRunOutcome:
        """Run one already-authorized PowerShell script."""
        ...

    def run_controlled(
        self,
        command: NormalizedPowerShellCommand,
        *,
        control: ExecutionTaskControl,
        on_started: Callable[[], None],
    ) -> ProcessRunOutcome: ...


class ProcessExecutableResolver(Protocol):
    def resolve(self, executable: str, *, cwd: str) -> str:
        """Resolve a bare executable using the same sanitized rules as the runner."""
        ...


class PowerShellExecutableResolver(Protocol):
    def resolve(self, executable: str) -> str:
        """Resolve the configured PowerShell 7 executable to an absolute path."""
        ...


class SensitiveValueRedactor(Protocol):
    def redact(self, value: str | None) -> str | None: ...

    def summarize_command(
        self,
        executable: str,
        args: tuple[str, ...],
        *,
        cwd: str,
    ) -> str: ...


class CommandPolicy(Protocol):
    def inspect_process(self, command: NormalizedProcessCommand) -> CommandInspection: ...

    def inspect_powershell(
        self, command: NormalizedPowerShellCommand, analysis: PowerShellAstAnalysis
    ) -> CommandInspection: ...


class PowerShellAnalyzer(Protocol):
    def analyze(self, script: str, *, timeout_seconds: float) -> PowerShellAstAnalysis: ...


class WorkspacePathResolver(Protocol):
    def resolve_within_root(
        self,
        path: str,
        *,
        expected_kind: Literal["file", "directory", "any"] = "directory",
        must_exist: bool = True,
    ) -> tuple[str, str]:
        """Return (absolute_path, relative_posix_path) constrained to the project root."""
        ...
