"""Inspect and optionally wait for a shell job."""

from __future__ import annotations

from typing import Any

from code_harness.errors import InvalidArgumentError
from code_harness.shell.background import (
    OUTPUT_CURSOR_START,
    TAIL_MAX_BYTES,
    JobRegistry,
    read_output_chunk,
    tail_output,
)

DEFAULT_TAIL_LINES = 50
MAX_TAIL_LINES = 500
MAX_WAIT_MS = 30_000


def get_job_status(
    registry: JobRegistry,
    *,
    job_id: str,
    wait_ms: int = 0,
    tail_lines: int = DEFAULT_TAIL_LINES,
    cursor: str | None = None,
) -> dict[str, Any]:
    if not job_id.strip():
        raise InvalidArgumentError("job_id must not be empty.")
    if not 0 <= wait_ms <= MAX_WAIT_MS:
        raise InvalidArgumentError(f"wait_ms must be between 0 and {MAX_WAIT_MS}.")
    if not 1 <= tail_lines <= MAX_TAIL_LINES:
        raise InvalidArgumentError(f"tail_lines must be between 1 and {MAX_TAIL_LINES}.")
    if cursor is not None and tail_lines != DEFAULT_TAIL_LINES:
        raise InvalidArgumentError("tail_lines cannot be combined with cursor.")

    job = registry.get(job_id)
    if job is None:
        if cursor is not None and cursor != OUTPUT_CURSOR_START:
            raise InvalidArgumentError("cursor is invalid or expired for this job.")
        if cursor is not None:
            return {
                "job_id": job_id,
                "status": "unknown",
                "pid": None,
                "exit_code": None,
                "elapsed_ms": 0,
                "output": "",
                "next_cursor": None,
                "has_more_output": False,
            }
        return {
            "job_id": job_id,
            "status": "unknown",
            "pid": None,
            "exit_code": None,
            "elapsed_ms": 0,
            "last_output": "",
            "output_truncated": False,
        }

    if wait_ms > 0 and job.process.poll() is None:
        job.wait(wait_ms / 1000)
    job.refresh()
    status = job.status
    exit_code = None if status in {"running", "cancelled"} else job.exit_code
    if cursor is not None:
        offset = (
            0
            if cursor == OUTPUT_CURSOR_START
            else registry.decode_output_cursor(cursor, job_id)
        )
        try:
            chunk = read_output_chunk(
                job.output_path,
                offset,
                final=status != "running",
                max_bytes=TAIL_MAX_BYTES,
            )
        except (OSError, ValueError) as error:
            raise InvalidArgumentError("cursor is invalid or expired for this job.") from error
        return {
            "job_id": job.job_id,
            "status": status,
            "pid": job.pid,
            "exit_code": exit_code,
            "elapsed_ms": job.elapsed_ms,
            "output": chunk.output,
            "next_cursor": registry.encode_output_cursor(job.job_id, chunk.next_offset),
            "has_more_output": chunk.has_more,
        }

    tail = tail_output(job.output_path, tail_lines, TAIL_MAX_BYTES)
    return {
        "job_id": job.job_id,
        "status": status,
        "pid": job.pid,
        "exit_code": exit_code,
        "elapsed_ms": job.elapsed_ms,
        "last_output": tail.output,
        "output_truncated": tail.truncated,
    }
