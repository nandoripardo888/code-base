from typing import Literal, Protocol

from code_harness.domain.models.execution import (
    CommandInspection,
    NormalizedPowerShellCommand,
    NormalizedProcessCommand,
    PowerShellAstAnalysis,
    ProcessRunOutcome,
)


class ProcessRunner(Protocol):
    def run(self, command: NormalizedProcessCommand) -> ProcessRunOutcome:
        """Run one already-authorized structured process."""
        ...


class ProcessExecutableResolver(Protocol):
    def resolve(self, executable: str, *, cwd: str) -> str:
        """Resolve a bare executable using the same sanitized rules as the runner."""
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
