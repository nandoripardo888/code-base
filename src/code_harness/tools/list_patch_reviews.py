"""Discover retained patch-review groups without exposing snapshots or diffs."""

from __future__ import annotations

from typing import Any

from code_harness.errors import InvalidArgumentError
from code_harness.review.service import REVIEW_FILTER_STATUSES, ReviewService

DEFAULT_REVIEW_LIMIT = 20
MAX_REVIEW_LIMIT = 100


def list_patch_reviews(
    reviews: ReviewService,
    *,
    limit: int = DEFAULT_REVIEW_LIMIT,
    status: str = "all",
) -> dict[str, Any]:
    if not 1 <= limit <= MAX_REVIEW_LIMIT:
        raise InvalidArgumentError(
            f"limit must be between 1 and {MAX_REVIEW_LIMIT}."
        )
    if status not in REVIEW_FILTER_STATUSES:
        choices = ", ".join(sorted(REVIEW_FILTER_STATUSES))
        raise InvalidArgumentError(f"status must be one of: {choices}.")

    listing = reviews.list_groups(limit=limit, status=status)
    items = [
        {
            "group_id": item.group_id,
            "group_title": item.group_title,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "patches_count": item.patches_count,
            "files_changed": item.files_changed,
            "pending_count": item.pending_count,
            "reviewed_count": item.reviewed_count,
            "rolled_back_count": item.rolled_back_count,
            "last_transaction_id": item.patches[-1].transaction_id,
        }
        for item in listing.items
    ]
    return {
        "status": status,
        "total": listing.total,
        "returned": len(items),
        "items": items,
    }
