from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from code_harness.application.changes.ordered_locks import OrderedSessionLocks
from code_harness.domain.enums import (
    ChangeIsolationMode,
    ChangeSegmentKind,
    ChangeSessionStatus,
    WorkspaceTopologyKind,
)
from code_harness.domain.errors import ChangeSessionUnsupportedTopologyError
from code_harness.domain.models.change_segment import (
    ChangeSessionSegment,
    GitChangeSegment,
    MirrorChangeSegment,
)
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.protocols.change_isolation import (
    GitBranchReader,
    GitWorktreeManager,
    SessionLockManager,
    WorkspaceMirrorPort,
)
from code_harness.domain.protocols.change_session_store import ChangeSessionStore
from code_harness.domain.protocols.workspace_topology import WorkspaceTopologyResolver


def git_lock_key(repository_root: str, target_branch: str) -> str:
    import os

    identity = os.path.normcase(str(Path(repository_root).resolve(strict=False)))
    return f"git:{identity}:{target_branch}"


def mirror_lock_key(workspace_id: str, relative_root: str) -> str:
    return f"mirror:{workspace_id}:{relative_root}"


class CreateChangeSessionTool:
    def __init__(
        self,
        *,
        workspace_id: str,
        workspace_root: Path,
        store: ChangeSessionStore,
        topology: WorkspaceTopologyResolver,
        sessions_home: Path,
        locks: SessionLockManager,
        worktrees: GitWorktreeManager,
        branch_reader: GitBranchReader,
        mirrors: WorkspaceMirrorPort | None = None,
        isolation_mode: ChangeIsolationMode = ChangeIsolationMode.AUTO,
    ) -> None:
        self._workspace_id = workspace_id
        self._workspace_root = Path(workspace_root)
        self._store = store
        self._topology = topology
        self._sessions_home = Path(sessions_home)
        self._locks = locks
        self._worktrees = worktrees
        self._branch_reader = branch_reader
        self._mirrors = mirrors
        self._isolation_mode = isolation_mode

    def run(self) -> ChangeSession:
        if self._isolation_mode is ChangeIsolationMode.IN_PLACE:
            raise ChangeSessionUnsupportedTopologyError("in_place")
        now = datetime.now(UTC).isoformat()
        session_id = uuid4().hex
        topology = self._topology.resolve(str(self._workspace_root))
        if topology.kind not in {
            WorkspaceTopologyKind.SINGLE_GIT,
            WorkspaceTopologyKind.NON_GIT,
            WorkspaceTopologyKind.COMPOSITE,
        }:
            raise ChangeSessionUnsupportedTopologyError(str(topology.kind))

        lock_keys = self._lock_keys(topology)
        with OrderedSessionLocks(self._locks, lock_keys):
            preparing = ChangeSession(
                session_id=session_id,
                workspace_id=self._workspace_id,
                workspace_root=str(self._workspace_root.resolve(strict=False)),
                topology_kind=topology.kind,
                status=ChangeSessionStatus.PREPARING,
                created_at=now,
                updated_at=now,
                expires_at=None,
                segments=(),
                candidate_digest=None,
                approval_id=None,
            )
            self._store.save_session(preparing)
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="session_created",
                    occurred_at=now,
                    session_id=session_id,
                )
            )
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="topology_resolved",
                    occurred_at=now,
                    session_id=session_id,
                    details={"kind": topology.kind.value},
                )
            )

            segments: list[ChangeSessionSegment] = []
            git_details: list[GitChangeSegment] = []
            mirror_details: list[MirrorChangeSegment] = []
            git_roots = tuple(topology.git_repositories)

            for topo_segment in topology.segments:
                if topo_segment.kind is ChangeSegmentKind.GIT_WORKTREE:
                    detail = self._worktrees.create(
                        session_id=session_id,
                        segment_id=topo_segment.segment_id,
                        repository_root=Path(topo_segment.source_root),
                        sessions_home=self._sessions_home,
                    )
                    self._store.append_event(
                        ChangeSessionEvent(
                            event_type="worktree_created",
                            occurred_at=datetime.now(UTC).isoformat(),
                            session_id=session_id,
                            segment_id=topo_segment.segment_id,
                            details={"worktree_path": detail.worktree_path},
                        )
                    )
                    git_details.append(detail)
                    segments.append(
                        ChangeSessionSegment(
                            segment_id=topo_segment.segment_id,
                            kind=ChangeSegmentKind.GIT_WORKTREE,
                            relative_root=topo_segment.relative_root,
                            source_root=topo_segment.source_root,
                            isolation_root=detail.worktree_path,
                            status=ChangeSessionStatus.READY.value,
                            base_digest=detail.base_sha,
                        )
                    )
                    continue

                if self._mirrors is None:
                    raise ChangeSessionUnsupportedTopologyError("workspace_mirror")
                include_roots = (
                    topology.loose_roots
                    if topology.kind is WorkspaceTopologyKind.COMPOSITE
                    else (".",)
                )
                detail = self._mirrors.create_with_manifest(
                    session_id=session_id,
                    segment_id=topo_segment.segment_id,
                    source_root=Path(topo_segment.source_root),
                    sessions_home=self._sessions_home,
                    include_relative_roots=include_roots,
                    exclude_git_roots=git_roots,
                )
                self._store.append_event(
                    ChangeSessionEvent(
                        event_type="mirror_created",
                        occurred_at=datetime.now(UTC).isoformat(),
                        session_id=session_id,
                        segment_id=topo_segment.segment_id,
                        details={"mirror_root": detail.mirror_root},
                    )
                )
                mirror_details.append(detail)
                segments.append(
                    ChangeSessionSegment(
                        segment_id=topo_segment.segment_id,
                        kind=ChangeSegmentKind.WORKSPACE_MIRROR,
                        relative_root=topo_segment.relative_root,
                        source_root=topo_segment.source_root,
                        isolation_root=detail.mirror_root,
                        status=ChangeSessionStatus.READY.value,
                        base_digest=detail.base_manifest_digest,
                    )
                )

            warnings: tuple[str, ...] = ()
            if topology.kind is WorkspaceTopologyKind.COMPOSITE:
                warnings = (
                    "Multi-repository integration is not atomic across segments.",
                )
            ready_at = datetime.now(UTC).isoformat()
            ready = ChangeSession(
                session_id=session_id,
                workspace_id=self._workspace_id,
                workspace_root=str(self._workspace_root.resolve(strict=False)),
                topology_kind=topology.kind,
                status=ChangeSessionStatus.READY,
                created_at=now,
                updated_at=ready_at,
                expires_at=None,
                segments=tuple(segments),
                candidate_digest=None,
                approval_id=None,
                warnings=warnings,
                git_details=tuple(git_details),
                mirror_details=tuple(mirror_details),
            )
            self._store.save_session(ready)
            return ready

    def _lock_keys(self, topology: object) -> tuple[str, ...]:
        keys: list[str] = []
        for segment in topology.segments:  # type: ignore[attr-defined]
            if segment.kind is ChangeSegmentKind.GIT_WORKTREE:
                branch = self._branch_reader.current_branch(Path(segment.source_root))
                keys.append(git_lock_key(segment.source_root, branch))
            else:
                keys.append(mirror_lock_key(self._workspace_id, segment.relative_root))
        return tuple(keys)


def aggregate_session_digest(segment_digests: tuple[str, ...]) -> str:
    payload = "|".join(segment_digests)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
