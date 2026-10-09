"""Cancel a background shell job without exposing process identifiers as control input."""

from __future__ import annotations

from typing import Any

from code_harness.errors import InvalidArgumentError
from code_harness.shell.background import JobRegistry


def cancel_job(registry: JobRegistry, *, job_id: str) -> dict[str, Any]:
    if not job_id.strip():
        raise InvalidArgumentError("job_id must not be empty.")

    job = registry.get(job_id)
    if job is None:
        return {
            "job_id": job_id,
            "status": "unknown",
            "exit_code": None,
            "elapsed_ms": 0,
            "already_finished": False,
        }

    initiated = job.cancel()
    status = job.status
    return {
        "job_id": job.job_id,
        "status": status,
        "exit_code": None if status == "cancelled" else job.exit_code,
        "elapsed_ms": job.elapsed_ms,
        "already_finished": not initiated,
    }
