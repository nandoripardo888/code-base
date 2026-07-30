"""Delete: remove a file, failing gracefully."""

from __future__ import annotations

from code_harness.errors import HarnessError
from code_harness.paths import PathGuard


def delete(guard: PathGuard, *, path: str) -> str:
    try:
        resolved = guard.resolve(path, kind="file")
    except HarnessError as error:
        return f"Could not delete {path}: {error.message}"

    relative = guard.relative(resolved)
    try:
        resolved.unlink()
    except OSError as error:
        return f"Could not delete {relative}: {error}"
    return f"Deleted {relative}."
