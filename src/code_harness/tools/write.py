"""Write: create or overwrite a file with the given contents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from code_harness.encoding import DEFAULT_WRITE_ENCODING, decode_file, encode_text, write_text
from code_harness.history import HistoryManager
from code_harness.paths import PathGuard
from code_harness.tools.change_tracking import (
    attach_review,
    normalize_description,
    record_single_file_change,
)

if TYPE_CHECKING:
    from code_harness.review import ReviewManager


def write(
    guard: PathGuard,
    history: HistoryManager | None = None,
    *,
    path: str,
    contents: str,
    description: str | None = None,
    group_id: str | None = None,
    group_title: str | None = None,
    reviews: ReviewManager | None = None,
) -> str | dict[str, object]:
    resolved = guard.resolve(path, kind="file", must_exist=False)
    existed = resolved.exists()
    decoded = decode_file(resolved) if existed else None
    encoding = decoded.encoding if decoded else DEFAULT_WRITE_ENCODING
    has_bom = decoded.has_bom if decoded else False
    if history is not None:
        normalized_description = normalize_description(description, required=True)
        assert normalized_description is not None
        before = resolved.read_bytes() if existed else None
        after = encode_text(contents, encoding, has_bom=has_bom)
        result = record_single_file_change(
            history,
            resolved=resolved,
            relative=guard.relative(resolved),
            operation="modify" if existed else "create",
            before=before,
            after=after,
            decoded=decoded,
            source_tool="write",
            description=normalized_description,
            group_id=group_id,
            group_title=group_title,
        )
        return attach_review(result, reviews)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    write_text(
        resolved,
        contents,
        encoding=encoding,
        has_bom=has_bom,
        atomic=True,
    )

    action = "Overwrote" if existed else "Created"
    line_count = len(contents.splitlines())
    return f"{action} {guard.relative(resolved)} ({line_count} lines, {len(contents)} chars)."
