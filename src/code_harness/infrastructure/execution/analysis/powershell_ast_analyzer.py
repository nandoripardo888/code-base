from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from code_harness.domain.errors import (
    PowerShellAnalysisError,
    PowerShellParseError,
    PowerShellUnavailableError,
)
from code_harness.domain.models.execution import PowerShellAstAnalysis
from code_harness.infrastructure.execution.runners.powershell_executable import (
    PowerShell7ExecutableResolver,
)


class PowerShellAstAnalyzer:
    def __init__(self, executable: str = "pwsh") -> None:
        self._executable = executable
        self._parser_script = Path(__file__).with_name("powershell_parser.ps1")

    def analyze(self, script: str, *, timeout_seconds: float) -> PowerShellAstAnalysis:
        executable = PowerShell7ExecutableResolver().resolve(self._executable)
        try:
            completed = subprocess.run(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(self._parser_script),
                ],
                input=json.dumps({"script": script}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self._parser_script.parent),
                timeout=min(max(timeout_seconds, 5.0), 10.0),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise PowerShellAnalysisError("PowerShell AST analysis timed out.") from error
        except OSError as error:
            raise PowerShellUnavailableError(self._executable, reason=str(error)) from error

        if completed.returncode != 0:
            raise PowerShellAnalysisError("PowerShell AST analysis worker failed.")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise PowerShellAnalysisError(
                "PowerShell AST analysis returned invalid output."
            ) from error
        if not isinstance(payload, dict):
            raise PowerShellAnalysisError("PowerShell AST analysis returned an invalid payload.")
        if int(payload.get("major_version", 0)) < 7:
            raise PowerShellUnavailableError(
                self._executable, reason="PowerShell 7 or later is required"
            )
        diagnostics = _string_tuple(payload.get("parse_errors"))
        if diagnostics:
            raise PowerShellParseError(diagnostics)
        return PowerShellAstAnalysis(
            commands=_string_tuple(payload.get("commands")),
            text_fragments=_string_tuple(payload.get("text_fragments")),
            dynamic_features=_string_tuple(payload.get("dynamic_features")),
        )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return ()
    return tuple(dict.fromkeys(value))
