from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChangeCheckpointFile:
    path: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None
    before_blob_id: str | None
    after_blob_id: str | None
    before_mode: int | None = None
    after_mode: int | None = None
    old_path: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeCheckpoint:
    checkpoint_id: str
    session_id: str
    segment_id: str
    parent_checkpoint_id: str | None
    sequence: int
    state: str
    kind: str
    patch_sha256: str | None
    created_at: str
    files: tuple[ChangeCheckpointFile, ...] = ()


@dataclass(frozen=True, slots=True)
class ChangePatchResult:
    session_id: str
    segment_id: str
    checkpoint_id: str
    parent_checkpoint_id: str | None
    patch_sha256: str | None
    changed_files: tuple[str, ...]
    active_checkpoint_id: str
    status: str
