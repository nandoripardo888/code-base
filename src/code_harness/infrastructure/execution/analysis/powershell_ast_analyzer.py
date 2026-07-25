from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from code_harness.domain.errors import (
    PowerShellAnalysisError,
    PowerShellParseError,
    PowerShellUnavailableError,
)
from code_harness.domain.models.execution import PowerShellAstAnalysis


class PowerShellAstAnalyzer:
    def __init__(self, executable: str = "pwsh") -> None:
        self._executable = executable
        self._parser_script = Path(__file__).with_name("powershell_parser.ps1")

    def analyze(self, script: str, *, timeout_seconds: float) -> PowerShellAstAnalysis:
        executable = _resolve_executable(self._executable)
        if executable is None:
            raise PowerShellUnavailableError(self._executable)
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
                timeout=min(timeout_seconds, 10.0),
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


def _resolve_executable(executable: str) -> str | None:
    resolved = shutil.which(executable)
    if resolved is not None:
        return resolved
    if os.name == "nt" and executable.casefold() in {"pwsh", "pwsh.exe"}:
        app_alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps/pwsh.exe"
        if app_alias.is_file():
            return str(app_alias)
    return None
