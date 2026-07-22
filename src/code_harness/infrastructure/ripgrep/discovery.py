"""Ripgrep executable discovery and health probing."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RipgrepProbe:
    configured_executable: str
    resolved_path: str | None
    version: str | None
    execution_test: str
    root_cause: str | None
    remediation: tuple[str, ...]
    affected_tools: tuple[str, ...]
    unaffected_tools: tuple[str, ...]


_AFFECTED_TOOLS = (
    "search_regex",
    "lexical reference expansion",
)
_UNAFFECTED_TOOLS = (
    "search_text via FTS",
    "find_symbol",
    "get_file_outline",
    "read_file",
    "read_range",
)
_REMEDIATION = (
    "Install Ripgrep.",
    "Restart the terminal or service after updating PATH.",
    "Or configure CODE_HARNESS_RG with the full path to rg.exe.",
)


def resolve_ripgrep_executable(
    *,
    explicit: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Resolve Ripgrep using explicit override, CODE_HARNESS_RG, then PATH."""
    environ = env if env is not None else os.environ
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    configured = environ.get("CODE_HARNESS_RG")
    if configured:
        candidates.append(configured)
    candidates.extend(["rg", "rg.exe"] if os.name == "nt" else ["rg"])

    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved = _resolve_candidate(normalized)
        if resolved is not None:
            return resolved
    return candidates[-1] if candidates else "rg"


def _resolve_candidate(candidate: str) -> str | None:
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve(strict=False))
    found = shutil.which(candidate)
    return found


def probe_ripgrep(
    executable: str,
    *,
    timeout_seconds: float = 5.0,
) -> RipgrepProbe:
    configured = executable
    resolved = _resolve_candidate(executable)
    if resolved is None:
        return RipgrepProbe(
            configured_executable=configured,
            resolved_path=None,
            version=None,
            execution_test="failed",
            root_cause="Executable not found in PATH.",
            remediation=_REMEDIATION,
            affected_tools=_AFFECTED_TOOLS,
            unaffected_tools=_UNAFFECTED_TOOLS,
        )

    path = Path(resolved)
    if not os.access(path, os.X_OK) and os.name != "nt":
        return RipgrepProbe(
            configured_executable=configured,
            resolved_path=resolved,
            version=None,
            execution_test="failed",
            root_cause="Resolved path is not executable.",
            remediation=_REMEDIATION,
            affected_tools=_AFFECTED_TOOLS,
            unaffected_tools=_UNAFFECTED_TOOLS,
        )

    version = _read_version(resolved, timeout_seconds=timeout_seconds)
    if version is None:
        return RipgrepProbe(
            configured_executable=configured,
            resolved_path=resolved,
            version=None,
            execution_test="failed",
            root_cause="Failed to execute rg --version.",
            remediation=_REMEDIATION,
            affected_tools=_AFFECTED_TOOLS,
            unaffected_tools=_UNAFFECTED_TOOLS,
        )

    search_ok, search_cause = _run_minimal_search(resolved, timeout_seconds=timeout_seconds)
    if not search_ok:
        return RipgrepProbe(
            configured_executable=configured,
            resolved_path=resolved,
            version=version,
            execution_test="failed",
            root_cause=search_cause or "Minimal search probe failed.",
            remediation=_REMEDIATION,
            affected_tools=_AFFECTED_TOOLS,
            unaffected_tools=_UNAFFECTED_TOOLS,
        )

    return RipgrepProbe(
        configured_executable=configured,
        resolved_path=resolved,
        version=version,
        execution_test="passed",
        root_cause=None,
        remediation=(),
        affected_tools=_AFFECTED_TOOLS,
        unaffected_tools=_UNAFFECTED_TOOLS,
    )


def _read_version(executable: str, *, timeout_seconds: float) -> str | None:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    line = completed.stdout.strip().splitlines()
    return line[0] if line else None


def _run_minimal_search(
    executable: str, *, timeout_seconds: float
) -> tuple[bool, str | None]:
    with tempfile.TemporaryDirectory(prefix="code-harness-rg-") as directory:
        sample = Path(directory) / "sample.txt"
        sample.write_text("code-harness ripgrep probe\n", encoding="utf-8")
        try:
            completed = subprocess.run(
                [executable, "--fixed-strings", "--max-count", "1", "probe", str(sample)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "Execution exceeded timeout."
        except (FileNotFoundError, OSError) as error:
            return False, str(error)
        if completed.returncode not in (0, 1):
            stderr = completed.stderr.strip() or f"returncode={completed.returncode}"
            return False, stderr
        return True, None
