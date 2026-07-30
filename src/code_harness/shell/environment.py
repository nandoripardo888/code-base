"""Shell discovery, command construction, and syntax diagnostics."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, cast

from code_harness.errors import (
    InvalidArgumentError,
    ShellSyntaxMismatchError,
    ShellUnavailableError,
)

ShellName = Literal["auto", "powershell", "cmd", "bash", "sh"]
ResolvedShellName = Literal["powershell", "cmd", "bash", "sh"]

SHELL_NAMES = frozenset({"auto", "powershell", "cmd", "bash", "sh"})
_POWERSHELL_EXIT_FORWARDING = "; if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }"
_BASH_HEREDOC = re.compile(r"(?:^|[;&|]\s*|\s)<<-?\s*['\"]?[A-Za-z_][\w-]*", re.MULTILINE)
_POWERSHELL_MARKERS = (
    re.compile(r"\$env\s*:", re.IGNORECASE),
    re.compile(r"\$(?:PSVersionTable|LASTEXITCODE)\b", re.IGNORECASE),
    re.compile(
        r"(?:^|[;|]\s*)(?:Get-Content|Set-Content|Start-Sleep|Write-Host)\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"@['\"]\s*(?:\r?\n)", re.MULTILINE),
)


@dataclass(frozen=True, slots=True)
class ShellEnvironment:
    os_name: str
    shell_name: ResolvedShellName
    shell_executable: str
    shell_version: str | None
    working_directory: str


def resolve_environment(
    requested_shell: str,
    *,
    working_directory: str,
) -> ShellEnvironment:
    """Resolve an available shell without invoking commands through ``shell=True``."""
    normalized = requested_shell.strip().lower()
    if normalized not in SHELL_NAMES:
        choices = ", ".join(sorted(SHELL_NAMES))
        raise InvalidArgumentError(f"shell must be one of: {choices}.")

    shell_name, executable = _resolve_shell(cast(ShellName, normalized))
    return ShellEnvironment(
        os_name=platform.system() or os.name,
        shell_name=shell_name,
        shell_executable=executable,
        shell_version=_shell_version(shell_name, executable),
        working_directory=working_directory,
    )


def build_shell_argv(command: str, environment: ShellEnvironment) -> list[str] | str:
    """Build argv for a resolved shell."""
    executable = environment.shell_executable
    if environment.shell_name == "powershell":
        return [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"& {{ {command}\n}}{_POWERSHELL_EXIT_FORWARDING}",
        ]
    if environment.shell_name == "cmd":
        prefix = subprocess.list2cmdline([executable, "/d", "/s", "/c"])
        return f'{prefix} "{command}"'
    return [executable, "-c", command]


def validate_command_syntax(command: str, shell_name: ResolvedShellName) -> tuple[str, ...]:
    """Reject only unmistakable shell mismatches and warn on ambiguous redirection."""
    if shell_name in {"powershell", "cmd"} and _BASH_HEREDOC.search(command):
        raise ShellSyntaxMismatchError(
            f"The command appears to use Bash heredoc syntax, but the active shell is "
            f"{_display_name(shell_name)}."
        )

    if shell_name in {"bash", "sh", "cmd"} and any(
        marker.search(command) for marker in _POWERSHELL_MARKERS
    ):
        raise ShellSyntaxMismatchError(
            f"The command appears to use PowerShell syntax, but the active shell is "
            f"{_display_name(shell_name)}."
        )

    if shell_name in {"powershell", "cmd"} and "<<" in command:
        return (
            f"The command contains '<<', which may be incompatible with "
            f"{_display_name(shell_name)}.",
        )
    return ()


def _resolve_shell(requested_shell: ShellName) -> tuple[ResolvedShellName, str]:
    if requested_shell != "auto":
        executable = _find_explicit(requested_shell)
        if executable is None:
            raise ShellUnavailableError(requested_shell)
        return requested_shell, executable

    override = os.environ.get("CODE_HARNESS_SHELL")
    if override:
        executable = _find_executable(override)
        if executable is None:
            raise ShellUnavailableError(override)
        return _classify_override(executable), executable

    if os.name == "nt":
        for executable_name, shell_name in (
            ("pwsh", "powershell"),
            ("powershell", "powershell"),
            ("cmd", "cmd"),
        ):
            executable = _find_executable(executable_name)
            if executable is not None:
                return cast(ResolvedShellName, shell_name), executable
    else:
        configured = os.environ.get("SHELL")
        if configured:
            executable = _find_executable(configured)
            basename = Path(configured).name.lower()
            if executable is not None and basename in {"bash", "sh"}:
                return cast(ResolvedShellName, basename), executable
        for shell_name in ("bash", "sh"):
            executable = _find_executable(shell_name)
            if executable is not None:
                return cast(ResolvedShellName, shell_name), executable

    raise ShellUnavailableError("auto")


def _find_explicit(shell_name: ResolvedShellName) -> str | None:
    candidates: tuple[str, ...]
    if shell_name == "powershell":
        candidates = ("pwsh", "powershell")
    elif shell_name == "cmd":
        candidates = ("cmd", "cmd.exe")
    else:
        candidates = (shell_name,)
    for candidate in candidates:
        executable = _find_executable(candidate)
        if executable is not None:
            return executable
    return None


def _find_executable(candidate: str) -> str | None:
    expanded = Path(candidate).expanduser()
    if expanded.parent != Path("."):
        return str(expanded.resolve(strict=False)) if expanded.is_file() else None
    return shutil.which(candidate)


def _classify_override(executable: str) -> ResolvedShellName:
    basename = Path(executable).stem.lower()
    if basename in {"pwsh", "powershell"}:
        return "powershell"
    if basename == "cmd":
        return "cmd"
    if basename == "bash":
        return "bash"
    if basename == "sh":
        return "sh"
    # Preserve the historical override behavior: Windows overrides used
    # PowerShell arguments and Unix overrides used POSIX ``-c`` arguments.
    return "powershell" if os.name == "nt" else "sh"


@cache
def _shell_version(shell_name: ResolvedShellName, executable: str) -> str | None:
    if shell_name == "powershell":
        argv = [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$PSVersionTable.PSVersion.ToString()",
        ]
    elif shell_name == "cmd":
        argv = [executable, "/d", "/c", "ver"]
    else:
        argv = [executable, "--version"]

    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).decode("utf-8", errors="replace").strip()
    return output.splitlines()[0] if output else None


def _display_name(shell_name: ResolvedShellName) -> str:
    return {
        "powershell": "PowerShell",
        "cmd": "CMD",
        "bash": "Bash",
        "sh": "SH",
    }[shell_name]
