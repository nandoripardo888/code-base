"""Serializable review-domain models kept independent from HTTP and MCP."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ReviewFileSummary:
    path: str
    operation: str
    additions: int
    deletions: int
    binary: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReviewListFile:
    index: int
    path: str
    operation: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReviewListItem:
    transaction_id: str
    source_tool: str
    status: str
    review_state: str
    created_at: str
    reviewed_at: str | None
    description: str | None
    files_changed: int
    additions: int | None
    deletions: int | None
    files: tuple[ReviewListFile, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["files"] = [item.to_dict() for item in self.files]
        return value


@dataclass(frozen=True, slots=True)
class ReviewList:
    items: tuple[ReviewListItem, ...]
    total: int
    retained_limit: int

    def to_dict(self) -> dict[str, object]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total": self.total,
            "retained_limit": self.retained_limit,
        }


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    transaction_id: str
    source_tool: str
    status: str
    review_state: str
    created_at: str
    reviewed_at: str | None
    description: str | None
    files_changed: int
    additions: int
    deletions: int
    files: tuple[ReviewFileSummary, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["files"] = [item.to_dict() for item in self.files]
        return value


@dataclass(frozen=True, slots=True)
class SideBySideRow:
    kind: str
    old_line_number: int | None
    old_text: str | None
    new_line_number: int | None
    new_text: str | None
    hidden_lines: int | None = None
    old_start: int | None = None
    new_start: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReviewFileDiff:
    index: int
    path: str
    operation: str
    additions: int
    deletions: int
    binary: bool
    rows: tuple[SideBySideRow, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["rows"] = [item.to_dict() for item in self.rows]
        return value
