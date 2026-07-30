"""Ripgrep discovery and invocation.

Both ``Grep`` and ``Glob`` delegate to ripgrep, which already honours
``.gitignore`` and skips binary files. There is no Python fallback.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from code_harness.encoding import decode_bytes
from code_harness.errors import RipgrepUnavailableError

DEFAULT_TIMEOUT_SECONDS = 60.0

# Exit code 1 means "no matches", which is a normal outcome, not a failure.
_NO_MATCH_EXIT_CODE = 1


def resolve_executable(*, explicit: str | None = None) -> str:
    """Resolve ripgrep from an explicit path, then CODE_HARNESS_RG, then PATH."""
    candidates = [
        explicit,
        os.environ.get("CODE_HARNESS_RG"),
        *(["rg", "rg.exe"] if os.name == "nt" else ["rg"]),
    ]
    for candidate in candidates:
        if not candidate or not candidate.strip():
            continue
        resolved = _resolve_candidate(candidate.strip())
        if resolved is not None:
            return resolved
    raise RipgrepUnavailableError("ripgrep was not found.")


def _resolve_candidate(candidate: str) -> str | None:
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve(strict=False))
    return shutil.which(candidate)


def run(
    arguments: list[str],
    *,
    cwd: Path,
    executable: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Run ripgrep and return stdout, raising on anything but success/no-match."""
    binary = executable or resolve_executable()
    try:
        completed = subprocess.run(
            [binary, *arguments],
            cwd=str(cwd),
            # Without this ripgrep searches stdin, which under the MCP server is
            # the client pipe, and the call never returns.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RipgrepUnavailableError(
            f"ripgrep exceeded {timeout_seconds:g}s and was terminated."
        ) from error
    except OSError as error:
        raise RipgrepUnavailableError(f"ripgrep could not be executed: {error}.") from error

    if completed.returncode not in (0, _NO_MATCH_EXIT_CODE):
        detail = decode_bytes(completed.stderr).text.strip() or f"exit code {completed.returncode}"
        raise RipgrepUnavailableError(f"ripgrep failed: {detail}.")
    return decode_bytes(completed.stdout).text
