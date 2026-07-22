"""Stable cursor helpers for repository listings."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from code_harness.domain.errors import CursorStaleError


@dataclass(frozen=True, slots=True)
class ListingCursor:
    index_revision: str
    query_hash: str
    sort_field: str
    sort_direction: str
    last_sort_value: str | None
    last_path: str | None


def query_hash(**parts: object) -> str:
    material = json.dumps(parts, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def encode_cursor(cursor: ListingCursor) -> str:
    payload = {
        "index_revision": cursor.index_revision,
        "query_hash": cursor.query_hash,
        "sort_field": cursor.sort_field,
        "sort_direction": cursor.sort_direction,
        "last_sort_value": cursor.last_sort_value,
        "last_path": cursor.last_path,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> ListingCursor:
    padding = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(value + padding)
        payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
        return ListingCursor(
            index_revision=str(payload["index_revision"]),
            query_hash=str(payload["query_hash"]),
            sort_field=str(payload["sort_field"]),
            sort_direction=str(payload["sort_direction"]),
            last_sort_value=(
                None if payload.get("last_sort_value") is None else str(payload["last_sort_value"])
            ),
            last_path=None if payload.get("last_path") is None else str(payload["last_path"]),
        )
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        raise CursorStaleError("Pagination cursor is invalid.") from error


def assert_cursor_compatible(
    cursor: ListingCursor,
    *,
    index_revision: str,
    expected_query_hash: str,
    sort_field: str,
    sort_direction: str,
) -> None:
    if (
        cursor.index_revision != index_revision
        or cursor.query_hash != expected_query_hash
        or cursor.sort_field != sort_field
        or cursor.sort_direction != sort_direction
    ):
        raise CursorStaleError("Pagination cursor is stale for the current index.")
