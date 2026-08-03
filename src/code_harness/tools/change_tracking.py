"""Shared metadata, snapshot, and review helpers for mutating tools."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

from code_harness.encoding import DecodedText, atomic_write_bytes
from code_harness.errors import InvalidArgumentError, PatchApplyError, PatchConflictError
from code_harness.history import FileSnapshot, HistoryManager, PatchGroupManifest

if TYPE_CHECKING:
    from code_harness.review import ReviewManager


def normalize_description(description: str | None, *, required: bool) -> str | None:
    normalized = " ".join(description.split()) if description is not None else ""
    if not normalized:
        if required:
            raise InvalidArgumentError(
                "description is required for persisted changes and must explain this update."
            )
        return None
    if len(normalized) > 500:
        raise InvalidArgumentError("description must be at most 500 characters.")
    return normalized


def normalize_group_title(title: str | None) -> str | None:
    normalized = " ".join(title.split()) if title is not None else ""
    if not normalized:
        return None
    if len(normalized) > 120:
        raise InvalidArgumentError("group_title must be at most 120 characters.")
    return normalized


def resolve_group(
    history: HistoryManager,
    *,
    group_id: str | None,
    group_title: str | None,
) -> PatchGroupManifest:
    normalized_id = group_id.strip() if group_id is not None else ""
    normalized_title = normalize_group_title(group_title)
    if normalized_id:
        if normalized_title is not None:
            raise InvalidArgumentError(
                "group_title is only accepted when creating a group; reuse group_id alone."
            )
        return history.load_group(normalized_id)
    if normalized_title is None:
        raise InvalidArgumentError(
            "group_title is required when group_id is empty; reuse the returned group_id "
            "for later changes."
        )
    return history.create_group(normalized_title)


def make_snapshot(
    history: HistoryManager,
    *,
    path: str,
    operation: str,
    before: bytes | None,
    after: bytes | None,
    decoded: DecodedText | None,
) -> FileSnapshot:
    return FileSnapshot(
        path=path,
        operation=operation,
        existed_before=before is not None,
        exists_after=after is not None,
        before_sha256=_sha256(before),
        after_sha256=_sha256(after),
        before_object=history.store_object(before) if before is not None else None,
        after_object=history.store_object(after) if after is not None else None,
        encoding=decoded.encoding if decoded else "utf-8",
        has_bom=decoded.has_bom if decoded else False,
        line_ending=decoded.line_ending if decoded else "LF",
    )


def record_single_file_change(
    history: HistoryManager,
    *,
    resolved: Path,
    relative: str,
    operation: str,
    before: bytes | None,
    after: bytes | None,
    decoded: DecodedText | None,
    source_tool: str,
    description: str,
    group_id: str | None,
    group_title: str | None,
) -> dict[str, object]:
    if before == after:
        raise InvalidArgumentError(f"The requested operation would not change {relative}.")
    with history.exclusive():
        group = resolve_group(
            history,
            group_id=group_id,
            group_title=group_title,
        )
        snapshot = make_snapshot(
            history,
            path=relative,
            operation=operation,
            before=before,
            after=after,
            decoded=decoded,
        )
        manifest = history.begin(
            None,
            (snapshot,),
            git_version=None,
            source_tool=source_tool,
            description=description,
            group_id=group.group_id,
        )
        manifest = history.update(manifest, status="ready")
        if not _matches(resolved, before):
            history.update(manifest, status="not_applied", error="File changed before commit.")
            raise PatchConflictError([relative])
        manifest = history.update(manifest, status="applying")
        try:
            _write_state(resolved, after)
        except OSError as error:
            restored = _restore_state(resolved, before)
            history.update(
                manifest,
                status="failed_and_restored" if restored else "recovery_required",
                error=str(error),
            )
            raise PatchApplyError(f"Could not commit change to {relative}: {error}") from error
        history.update(manifest, status="applied")
        history.touch_group(group.group_id)
        return {
            "status": "applied",
            "group_id": group.group_id,
            "group_title": group.group_title,
            "transaction_id": manifest.transaction_id,
            "source_tool": source_tool,
            "description": description,
            "files_changed": 1,
            "files": [{"path": relative, "operation": operation}],
            "history_saved": True,
        }


def attach_review(
    result: dict[str, object],
    reviews: ReviewManager | None,
) -> dict[str, object]:
    transaction_id = result.get("transaction_id")
    if reviews is None or not isinstance(transaction_id, str):
        return result
    try:
        review = reviews.open(
            transaction_id,
            open_browser=os.environ.get("CODE_HARNESS_REVIEW_AUTO_OPEN", "").lower()
            in {"1", "true", "yes", "on"},
        )
        result["review"] = review
        result["review_available"] = True
        result["review_url"] = review["url"]
        result["review_message"] = "Abra o portal local para revisar esta alteração."
    except Exception as error:
        result["review"] = {"available": False}
        result["review_available"] = False
        result["review_error"] = str(error)
    return result


def _matches(path: Path, expected: bytes | None) -> bool:
    if expected is None:
        return not path.is_file()
    try:
        return path.is_file() and path.read_bytes() == expected
    except OSError:
        return False


def _write_state(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink()
    else:
        atomic_write_bytes(path, content)


def _restore_state(path: Path, content: bytes | None) -> bool:
    try:
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(path, content)
    except OSError:
        return False
    return True


def _sha256(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None
