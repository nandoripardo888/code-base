from dataclasses import dataclass

from code_harness.domain.enums import ChangeSourceKind, FileChangeKind


@dataclass(frozen=True, slots=True)
class ChangedHunk:
    hunk_id: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    kind: FileChangeKind
    old_path: str | None = None
    binary: bool = False
    old_sha256: str | None = None
    new_sha256: str | None = None
    hunks: tuple[ChangedHunk, ...] = ()


@dataclass(frozen=True, slots=True)
class ChangeSet:
    change_set_id: str
    source_kind: ChangeSourceKind
    repository_id: str
    base_ref: str | None
    base_sha: str | None
    head_ref: str | None
    head_sha: str | None
    diff_sha256: str
    files: tuple[ChangedFile, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class ChangeDiff:
    change_set_id: str
    files: tuple[ChangedFile, ...]
    unified_text: str


@dataclass(frozen=True, slots=True)
class ChangedSymbol:
    symbol_id: str
    path: str
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    hunk_ids: tuple[str, ...]
    side: str


@dataclass(frozen=True, slots=True)
class ChangeSetRequest:
    source: ChangeSourceKind = ChangeSourceKind.WORKING_TREE
    base: str | None = "HEAD"
    include_untracked: bool = True
