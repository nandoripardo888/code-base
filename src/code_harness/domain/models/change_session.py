from __future__ import annotations

from dataclasses import dataclass, field

from code_harness.domain.enums import ChangeSessionStatus, WorkspaceTopologyKind
from code_harness.domain.models.change_segment import (
    ChangeSessionSegment,
    GitChangeSegment,
    MirrorChangeSegment,
)


@dataclass(frozen=True, slots=True)
class ChangeSession:
    session_id: str
    workspace_id: str
    workspace_root: str
    topology_kind: WorkspaceTopologyKind | str
    status: ChangeSessionStatus
    created_at: str
    updated_at: str
    expires_at: str | None
    segments: tuple[ChangeSessionSegment, ...]
    candidate_digest: str | None
    approval_id: str | None
    warnings: tuple[str, ...] = ()
    git_details: tuple[GitChangeSegment, ...] = ()
    mirror_details: tuple[MirrorChangeSegment, ...] = ()


@dataclass(frozen=True, slots=True)
class CompositeChangeSession:
    session_id: str
    segments: tuple[ChangeSessionSegment, ...]
    status: ChangeSessionStatus


@dataclass(frozen=True, slots=True)
class ChangeSessionEvent:
    event_type: str
    occurred_at: str
    session_id: str
    segment_id: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChangeSessionDiff:
    session_id: str
    candidate_digest: str | None
    segment_id: str
    unified_text: str
    files: tuple[str, ...]
