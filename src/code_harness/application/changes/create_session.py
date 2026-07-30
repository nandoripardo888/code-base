from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import perf_counter_ns
from uuid import uuid4

from code_harness.application.changes.ordered_locks import OrderedSessionLocks
from code_harness.domain.enums import (
    ChangeIsolationMode,
    ChangeSegmentKind,
    ChangeSessionStatus,
    WorkspaceTopologyKind,
)
from code_harness.domain.errors import (
    ChangeSessionPathRejectedError,
    ChangeSessionUnsupportedTopologyError,
)
from code_harness.domain.models.change_checkpoint import ChangeCheckpoint
from code_harness.domain.models.change_segment import (
    ChangeSessionSegment,
    GitChangeSegment,
    MirrorChangeSegment,
)
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.models.workspace_topology import TopologySegment, WorkspaceTopology
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

    def run(self, *, paths: tuple[str, ...] | None = None) -> ChangeSession:
        total_started = perf_counter_ns()
        if self._isolation_mode is ChangeIsolationMode.IN_PLACE:
            raise ChangeSessionUnsupportedTopologyError("in_place")
        now = datetime.now(UTC).isoformat()
        session_id = uuid4().hex
        topology_started = perf_counter_ns()
        topology = self._topology.resolve(str(self._workspace_root))
        topology_ms = _elapsed_ms(topology_started)
        if topology.kind not in {
            WorkspaceTopologyKind.SINGLE_GIT,
            WorkspaceTopologyKind.NON_GIT,
            WorkspaceTopologyKind.COMPOSITE,
        }:
            raise ChangeSessionUnsupportedTopologyError(str(topology.kind))

        normalized_paths, selected_segments = _select_segments(topology, paths)
        lock_keys = self._lock_keys(selected_segments)
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
                    details={
                        "kind": topology.kind.value,
                        "elapsed_ms": topology_ms,
                        "paths": list(normalized_paths),
                        "selected_segments": [item.segment_id for item in selected_segments],
                    },
                )
            )

            segments: list[ChangeSessionSegment] = []
            git_details: list[GitChangeSegment] = []
            mirror_details: list[MirrorChangeSegment] = []
            git_roots = tuple(topology.git_repositories)

            for topo_segment in selected_segments:
                segment_started = perf_counter_ns()
                if topo_segment.kind is ChangeSegmentKind.GIT_WORKTREE:
                    git_detail = self._worktrees.create(
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
                            details={
                                "worktree_path": git_detail.worktree_path,
                                "elapsed_ms": _elapsed_ms(segment_started),
                                "timings_ms": git_detail.timings_ms or {},
                            },
                        )
                    )
                    git_details.append(git_detail)
                    segments.append(
                        ChangeSessionSegment(
                            segment_id=topo_segment.segment_id,
                            kind=ChangeSegmentKind.GIT_WORKTREE,
                            relative_root=topo_segment.relative_root,
                            source_root=topo_segment.source_root,
                            isolation_root=git_detail.worktree_path,
                            status=ChangeSessionStatus.READY.value,
                            base_digest=git_detail.base_sha,
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
                mirror_detail = self._mirrors.create_with_manifest(
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
                        details={
                            "mirror_root": mirror_detail.mirror_root,
                            "elapsed_ms": _elapsed_ms(segment_started),
                        },
                    )
                )
                mirror_details.append(mirror_detail)
                segments.append(
                    ChangeSessionSegment(
                        segment_id=topo_segment.segment_id,
                        kind=ChangeSegmentKind.WORKSPACE_MIRROR,
                        relative_root=topo_segment.relative_root,
                        source_root=topo_segment.source_root,
                        isolation_root=mirror_detail.mirror_root,
                        status=ChangeSessionStatus.READY.value,
                        base_digest=mirror_detail.base_manifest_digest,
                    )
                )

            warnings: tuple[str, ...] = ()
            if len(segments) > 1:
                warnings = ("Multi-repository integration is not atomic across segments.",)
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
            persistence_started = perf_counter_ns()
            self._store.save_session(ready)
            for segment in ready.segments:
                if segment.kind is not ChangeSegmentKind.GIT_WORKTREE:
                    continue
                checkpoint = ChangeCheckpoint(
                    checkpoint_id=uuid4().hex,
                    session_id=session_id,
                    segment_id=segment.segment_id,
                    parent_checkpoint_id=None,
                    sequence=0,
                    state="active",
                    kind="initial",
                    patch_sha256=None,
                    created_at=ready_at,
                )
                self._store.save_checkpoint(checkpoint)
                self._store.append_event(
                    ChangeSessionEvent(
                        event_type="checkpoint_created",
                        occurred_at=ready_at,
                        session_id=session_id,
                        segment_id=segment.segment_id,
                        details={
                            "checkpoint_id": checkpoint.checkpoint_id,
                            "kind": "initial",
                        },
                    )
                )
            persistence_ms = _elapsed_ms(persistence_started)
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="session_ready",
                    occurred_at=ready_at,
                    session_id=session_id,
                    details={
                        "elapsed_ms": _elapsed_ms(total_started),
                        "persistence_ms": persistence_ms,
                    },
                )
            )
            return ready

    def _lock_keys(self, segments: tuple[TopologySegment, ...]) -> tuple[str, ...]:
        keys: list[str] = []
        for segment in segments:
            if segment.kind is ChangeSegmentKind.GIT_WORKTREE:
                branch = self._branch_reader.current_branch(Path(segment.source_root))
                keys.append(git_lock_key(segment.source_root, branch))
            else:
                keys.append(mirror_lock_key(self._workspace_id, segment.relative_root))
        return tuple(keys)


def aggregate_session_digest(segment_digests: tuple[str, ...]) -> str:
    payload = "|".join(segment_digests)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _select_segments(
    topology: WorkspaceTopology,
    paths: tuple[str, ...] | None,
) -> tuple[tuple[str, ...], tuple[TopologySegment, ...]]:
    if paths is None:
        return (), topology.segments
    if not paths:
        raise ChangeSessionPathRejectedError("", "paths must not be empty")

    normalized = tuple(dict.fromkeys(_normalize_target_path(item) for item in paths))
    selected_ids: set[str] = set()
    for path in normalized:
        matches = [
            segment for segment in topology.segments if _segment_contains_path(segment, path)
        ]
        if not matches:
            raise ChangeSessionPathRejectedError(
                path,
                "path does not belong to a workspace segment",
            )
        selected = max(
            matches,
            key=lambda item: len(_relative_parts(item.relative_root)),
        )
        selected_ids.add(selected.segment_id)
    selected_segments = tuple(item for item in topology.segments if item.segment_id in selected_ids)
    return normalized, selected_segments


def _normalize_target_path(raw: str) -> str:
    value = raw.replace("\\", "/").strip()
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or ".." in pure.parts
        or (pure.parts and pure.parts[0].endswith(":"))
    ):
        raise ChangeSessionPathRejectedError(raw, "path must be workspace-relative")
    normalized = pure.as_posix()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == ".":
        raise ChangeSessionPathRejectedError(raw, "path must identify a workspace item")
    return normalized


def _segment_contains_path(segment: TopologySegment, path: str) -> bool:
    relative = segment.relative_root.replace("\\", "/").strip("/")
    if relative in {"", "."}:
        return True
    return path == relative or path.startswith(f"{relative}/")


def _relative_parts(relative_root: str) -> tuple[str, ...]:
    normalized = relative_root.replace("\\", "/").strip("/")
    return () if normalized in {"", "."} else PurePosixPath(normalized).parts


def _elapsed_ms(started_ns: int) -> float:
    return max(0.0, (perf_counter_ns() - started_ns) / 1_000_000.0)
