from __future__ import annotations

from dataclasses import dataclass

from code_harness.domain.enums import ChangeSegmentKind, WorkspaceTopologyKind


@dataclass(frozen=True, slots=True)
class TopologySegment:
    segment_id: str
    kind: ChangeSegmentKind
    relative_root: str
    source_root: str
    repository_root: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceTopology:
    workspace_root: str
    kind: WorkspaceTopologyKind
    git_repositories: tuple[str, ...]
    loose_roots: tuple[str, ...]
    segments: tuple[TopologySegment, ...]
