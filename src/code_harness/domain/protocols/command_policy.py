from typing import Literal, Protocol

from code_harness.domain.models.execution import (
    CommandInspection,
    NormalizedPowerShellCommand,
    NormalizedProcessCommand,
    PowerShellAstAnalysis,
)


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
