"""Delete: remove a file, failing gracefully."""

from __future__ import annotations

from typing import TYPE_CHECKING

from code_harness.encoding import decode_bytes
from code_harness.errors import HarnessError
from code_harness.history import HistoryManager
from code_harness.paths import PathGuard
from code_harness.tools.change_tracking import (
    attach_review,
    normalize_description,
    record_single_file_change,
)

if TYPE_CHECKING:
    from code_harness.review import ReviewManager


def delete(
    guard: PathGuard,
    history: HistoryManager | None = None,
    *,
    path: str,
    description: str | None = None,
    group_id: str | None = None,
    group_title: str | None = None,
    reviews: ReviewManager | None = None,
) -> str | dict[str, object]:
    try:
        resolved = guard.resolve(path, kind="file")
    except HarnessError as error:
        return f"Could not delete {path}: {error.message}"

    relative = guard.relative(resolved)
    if history is not None:
        normalized_description = normalize_description(description, required=True)
        assert normalized_description is not None
        before = resolved.read_bytes()
        result = record_single_file_change(
            history,
            resolved=resolved,
            relative=relative,
            operation="delete",
            before=before,
            after=None,
            decoded=decode_bytes(before),
            source_tool="delete",
            description=normalized_description,
            group_id=group_id,
            group_title=group_title,
        )
        return attach_review(result, reviews)
    try:
        resolved.unlink()
    except OSError as error:
        return f"Could not delete {relative}: {error}"
    return f"Deleted {relative}."
