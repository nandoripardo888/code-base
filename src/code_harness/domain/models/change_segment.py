from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from code_harness.domain.enums import ChangeSegmentKind
from code_harness.domain.models.change_set import ChangedFile


@dataclass(frozen=True, slots=True)
class ChangeSessionSegment:
    segment_id: str
    kind: ChangeSegmentKind
    relative_root: str
    source_root: str
    isolation_root: str
    status: str
    base_digest: str
    candidate_digest: str | None = None


@dataclass(frozen=True, slots=True)
class GitChangeSegment:
    repository_root: str
    git_common_dir: str
    target_branch: str
    base_sha: str
    temporary_branch: str
    worktree_path: str
    candidate_commit: str | None = None
    integration_strategy: str = "cherry_pick_v1"
    source_head_sha: str | None = None
    baseline_commit: str | None = None
    baseline_tree: str | None = None
    timings_ms: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MirrorChangeSegment:
    source_root: str
    mirror_root: str
    base_manifest_digest: str
    candidate_manifest_digest: str | None = None
    changed_files: tuple[ChangedFile, ...] = ()
