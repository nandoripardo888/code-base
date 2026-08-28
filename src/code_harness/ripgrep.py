"""Ripgrep discovery and invocation.

Both ``Grep`` and ``Glob`` delegate to ripgrep, which already honours
``.gitignore`` and skips binary files. There is no Python fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from code_harness.encoding import decode_bytes
from code_harness.errors import RipgrepUnavailableError

DEFAULT_TIMEOUT_SECONDS = 60.0

# Exit code 1 means "no matches", which is a normal outcome, not a failure.
_NO_MATCH_EXIT_CODE = 1
_TERMINATE_GRACE_SECONDS = 5.0


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
    max_stdout_lines: int | None = None,
    max_json_matches: int | None = None,
) -> str:
    """Run ripgrep and return stdout, raising on anything but success/no-match.

    When ``max_stdout_lines`` or ``max_json_matches`` is set, ripgrep is stopped
    as soon as enough output is collected so broad searches on large trees do
    not wait for a full scan (and hit the process timeout).
    """
    binary = executable or resolve_executable()
    bounded = max_stdout_lines is not None or max_json_matches is not None
    try:
        if bounded:
            return _run_bounded(
                binary,
                arguments,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_stdout_lines=max_stdout_lines,
                max_json_matches=max_json_matches,
            )
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
            f"ripgrep exceeded {timeout_seconds:g}s and was terminated. "
            "Narrow path/glob/type or lower the search scope.",
            hint_install=False,
        ) from error
    except OSError as error:
        raise RipgrepUnavailableError(f"ripgrep could not be executed: {error}.") from error

    return _decode_completed(completed)


def _run_bounded(
    binary: str,
    arguments: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    max_stdout_lines: int | None,
    max_json_matches: int | None,
) -> str:
    try:
        process = subprocess.Popen(
            [binary, *arguments],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise RipgrepUnavailableError(f"ripgrep could not be executed: {error}.") from error

    assert process.stdout is not None
    assert process.stderr is not None

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stopped_early = False
    line_count = 0
    match_count = 0
    read_error: Exception | None = None

    def _read_stdout() -> None:
        nonlocal stopped_early, line_count, match_count, read_error
        try:
            for line in process.stdout:
                stdout_chunks.append(line)
                line_count += 1
                if max_stdout_lines is not None and line_count >= max_stdout_lines:
                    stopped_early = True
                    _terminate(process)
                    return
                if max_json_matches is not None and _json_line_is_match(line):
                    match_count += 1
                    if match_count >= max_json_matches:
                        stopped_early = True
                        _terminate(process)
                        return
        except Exception as error:  # noqa: BLE001 - surface via parent thread
            read_error = error

    def _read_stderr() -> None:
        try:
            while True:
                chunk = process.stderr.read(65_536)
                if not chunk:
                    break
                stderr_chunks.append(chunk)
        except Exception as error:  # noqa: BLE001
            nonlocal read_error
            if read_error is None:
                read_error = error

    stdout_thread = threading.Thread(target=_read_stdout, name="rg-stdout", daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, name="rg-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            _terminate(process)
            break
        try:
            process.wait(timeout=min(remaining, 0.25))
            break
        except subprocess.TimeoutExpired:
            if stopped_early:
                break
            continue

    stdout_thread.join(timeout=_TERMINATE_GRACE_SECONDS)
    stderr_thread.join(timeout=_TERMINATE_GRACE_SECONDS)

    if read_error is not None:
        raise RipgrepUnavailableError(
            f"ripgrep could not be executed: {read_error}.",
            hint_install=False,
        ) from read_error

    if timed_out and not stopped_early:
        raise RipgrepUnavailableError(
            f"ripgrep exceeded {timeout_seconds:g}s and was terminated. "
            "Narrow path/glob/type or lower the search scope.",
            hint_install=False,
        )

    stdout = b"".join(stdout_chunks)
    stderr = b"".join(stderr_chunks)
    if stopped_early:
        return decode_bytes(stdout).text

    returncode = process.returncode if process.returncode is not None else -1
    if returncode not in (0, _NO_MATCH_EXIT_CODE):
        detail = decode_bytes(stderr).text.strip() or f"exit code {returncode}"
        raise RipgrepUnavailableError(f"ripgrep failed: {detail}.")
    return decode_bytes(stdout).text


def _json_line_is_match(line: bytes) -> bool:
    try:
        payload = json.loads(decode_bytes(line).text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("type") == "match"


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _decode_completed(completed: subprocess.CompletedProcess[bytes]) -> str:
    if completed.returncode not in (0, _NO_MATCH_EXIT_CODE):
        detail = decode_bytes(completed.stderr).text.strip() or f"exit code {completed.returncode}"
        raise RipgrepUnavailableError(f"ripgrep failed: {detail}.")
    return decode_bytes(completed.stdout).text
