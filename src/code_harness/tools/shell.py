"""Run a command predictably, returning a final result or an opaque job id."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from code_harness.errors import ExecutionError, InvalidArgumentError, PathOutsideProjectError
from code_harness.paths import PathGuard
from code_harness.shell.background import TAIL_MAX_BYTES, JobRegistry, ShellJob, tail_output
from code_harness.shell.environment import (
    ShellEnvironment,
    build_shell_argv,
    resolve_environment,
    validate_command_syntax,
)

DEFAULT_BLOCK_UNTIL_MS = 30_000
MAX_BLOCK_UNTIL_MS = 30_000
COMPLETED_OUTPUT_LINES = 500


def shell(
    guard: PathGuard,
    registry: JobRegistry,
    *,
    command: str,
    working_directory: str | None = None,
    block_until_ms: int = DEFAULT_BLOCK_UNTIL_MS,
    description: str | None = None,
    shell: str = "auto",
) -> dict[str, Any]:
    if not command.strip():
        raise InvalidArgumentError("command must not be empty.")
    if not 0 <= block_until_ms <= MAX_BLOCK_UNTIL_MS:
        raise InvalidArgumentError(
            f"block_until_ms must be between 0 and {MAX_BLOCK_UNTIL_MS}."
        )

    cwd = guard.resolve(working_directory or ".", kind="directory")
    if cwd != guard.root and guard.root not in cwd.parents:
        raise PathOutsideProjectError(working_directory or ".")

    environment = resolve_environment(shell, working_directory=guard.relative(cwd))
    warnings = validate_command_syntax(command, environment.shell_name)
    job = _launch(registry, command, cwd, environment)

    if block_until_ms > 0 and job.wait(block_until_ms / 1000):
        return _completed(job, environment, warnings, description)
    if job.process.poll() is not None:
        job.refresh()
        return _completed(job, environment, warnings, description)
    return _running(job, environment, warnings, description)


def _launch(
    registry: JobRegistry,
    command: str,
    cwd: Path,
    environment: ShellEnvironment,
) -> ShellJob:
    output_path = registry.prepare_output_path()
    started_at = time.monotonic()
    try:
        with output_path.open("wb") as output_file:
            process = subprocess.Popen(
                build_shell_argv(command, environment),
                cwd=str(cwd),
                stdout=output_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
                start_new_session=os.name != "nt",
            )
    except OSError as error:
        output_path.unlink(missing_ok=True)
        raise ExecutionError(f"Could not start the shell: {error}") from error

    job = ShellJob(
        job_id="",
        process=process,
        command=command,
        working_directory=cwd,
        shell_name=environment.shell_name,
        shell_executable=environment.shell_executable,
        started_at=started_at,
        output_path=output_path,
    )
    try:
        registry.register(job)
    except ExecutionError:
        job.terminate()
        output_path.unlink(missing_ok=True)
        raise
    return job


def _completed(
    job: ShellJob,
    environment: ShellEnvironment,
    warnings: tuple[str, ...],
    description: str | None,
) -> dict[str, Any]:
    tail = tail_output(job.output_path, COMPLETED_OUTPUT_LINES, TAIL_MAX_BYTES)
    result: dict[str, Any] = {
        "status": job.status,
        "job_id": None,
        "pid": job.pid,
        "exit_code": job.exit_code,
        "elapsed_ms": job.elapsed_ms,
        "output": tail.output,
        "output_truncated": tail.truncated,
        "environment": _environment_payload(environment),
    }
    return _with_optional_fields(result, warnings, description)


def _running(
    job: ShellJob,
    environment: ShellEnvironment,
    warnings: tuple[str, ...],
    description: str | None,
) -> dict[str, Any]:
    tail = tail_output(job.output_path, 50, TAIL_MAX_BYTES)
    result: dict[str, Any] = {
        "status": "running",
        "job_id": job.job_id,
        "pid": job.pid,
        "exit_code": None,
        "elapsed_ms": job.elapsed_ms,
        "last_output": tail.output,
        "output_truncated": tail.truncated,
        "environment": _environment_payload(environment),
    }
    return _with_optional_fields(result, warnings, description)


def _environment_payload(environment: ShellEnvironment) -> dict[str, str | None]:
    return {
        "os": environment.os_name,
        "shell": environment.shell_name,
        "shell_version": environment.shell_version,
        "cwd": environment.working_directory,
    }


def _with_optional_fields(
    result: dict[str, Any],
    warnings: tuple[str, ...],
    description: str | None,
) -> dict[str, Any]:
    if warnings:
        result["warnings"] = list(warnings)
    if description:
        result["description"] = description
    return result
