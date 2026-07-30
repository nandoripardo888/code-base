from __future__ import annotations

from pathlib import Path

from code_harness.domain.enums import ChangeSegmentKind, ChangeSessionStatus
from code_harness.domain.errors import (
    ChangeSessionInvalidStateError,
    ChangeSessionPathRejectedError,
    GitCommandFailedError,
)
from code_harness.domain.models.change_session import (
    ChangeSegmentDiff,
    ChangeSession,
    ChangeSessionDiff,
    ChangeSessionEvent,
)
from code_harness.domain.protocols.change_isolation import GitWorktreeManager, WorkspaceMirrorPort
from code_harness.domain.protocols.change_session_store import ChangeSessionStore


class InspectChangeSessionTool:
    def __init__(
        self,
        *,
        store: ChangeSessionStore,
        sessions_home: Path | None = None,
        worktrees: GitWorktreeManager | None = None,
        mirrors: WorkspaceMirrorPort | None = None,
    ) -> None:
        self._store = store
        self._sessions_home = Path(sessions_home) if sessions_home is not None else None
        self._worktrees = worktrees
        self._mirrors = mirrors

    def get(self, session_id: str) -> ChangeSession:
        return self._store.get_session(session_id)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        status: ChangeSessionStatus | None = None,
        limit: int = 50,
    ) -> tuple[ChangeSession, ...]:
        return self._store.list_sessions(
            workspace_id=workspace_id,
            status=status,
            limit=limit,
        )

    def events(self, session_id: str) -> tuple[ChangeSessionEvent, ...]:
        return self._store.list_events(session_id)

    def resolve_cwd(self, session_id: str, *, segment_id: str | None = None) -> Path:
        session = self._store.get_session(session_id)
        if session.status not in {
            ChangeSessionStatus.READY,
            ChangeSessionStatus.AGENT_WORKING,
        }:
            raise ChangeSessionInvalidStateError(
                f"Session cwd is frozen in status {session.status.value!r}.",
                session_id=session_id,
                status=session.status.value,
            )
        if not session.segments:
            raise ChangeSessionInvalidStateError(
                "Session has no segments.",
                session_id=session_id,
            )
        if segment_id is None:
            segment = session.segments[0]
        else:
            matches = [item for item in session.segments if item.segment_id == segment_id]
            if not matches:
                raise ChangeSessionPathRejectedError(
                    segment_id,
                    "unknown segment_id for session",
                )
            segment = matches[0]
        return Path(segment.isolation_root)

    def mark_agent_working(self, session_id: str) -> ChangeSession:
        from datetime import UTC, datetime

        session = self._store.get_session(session_id)
        if session.status is ChangeSessionStatus.AGENT_WORKING:
            return session
        if session.status is not ChangeSessionStatus.READY:
            raise ChangeSessionInvalidStateError(
                f"Cannot mark agent working in status {session.status.value!r}.",
                session_id=session_id,
                status=session.status.value,
            )
        now = datetime.now(UTC).isoformat()
        updated = self._store.update_status(
            session_id,
            ChangeSessionStatus.AGENT_WORKING,
            updated_at=now,
        )
        self._store.append_event(
            ChangeSessionEvent(
                event_type="agent_started",
                occurred_at=now,
                session_id=session_id,
            )
        )
        return updated

    def get_diff(self, session_id: str) -> ChangeSessionDiff:
        session = self._store.get_session(session_id)
        if session.candidate_digest is not None:
            return self._prepared_diff(session)
        return self._draft_diff(session)

    def _draft_diff(self, session: ChangeSession) -> ChangeSessionDiff:
        segment_diffs: list[ChangeSegmentDiff] = []
        warnings: list[str] = []
        for segment in session.segments:
            try:
                if segment.kind is ChangeSegmentKind.GIT_WORKTREE:
                    if self._worktrees is None:
                        warnings.append(f"diff_unavailable:{segment.segment_id}")
                        continue
                    unified, files = self._worktrees.preview_diff(
                        worktree_path=Path(segment.isolation_root)
                    )
                else:
                    detail = next(
                        (
                            item
                            for item in session.mirror_details
                            if item.mirror_root == segment.isolation_root
                        ),
                        None,
                    )
                    if detail is None or self._mirrors is None:
                        warnings.append(f"diff_unavailable:{segment.segment_id}")
                        continue
                    unified, files = self._mirrors.preview(
                        session_id=session.session_id,
                        segment_id=segment.segment_id,
                        source_root=Path(detail.source_root),
                        mirror_root=Path(detail.mirror_root),
                        base_manifest_digest=detail.base_manifest_digest,
                        sessions_home=self._sessions_home,
                    )
            except (OSError, GitCommandFailedError, ChangeSessionPathRejectedError):
                warnings.append(f"diff_unavailable:{segment.segment_id}")
                continue
            if not files and not unified:
                continue
            segment_diffs.append(
                ChangeSegmentDiff(
                    segment_id=segment.segment_id,
                    relative_root=segment.relative_root,
                    unified_text=unified,
                    files=files,
                )
            )
        return _compose_diff(
            session,
            state="draft",
            segment_diffs=tuple(segment_diffs),
            warnings=tuple(warnings),
        )

    def _prepared_diff(self, session: ChangeSession) -> ChangeSessionDiff:
        segment_diffs: list[ChangeSegmentDiff] = []
        warnings: list[str] = []
        for segment in session.segments:
            if segment.candidate_digest is None:
                continue
            stored = self._store.get_diff(
                session.session_id,
                segment.segment_id,
                segment.candidate_digest,
            )
            if stored is not None:
                segment_diffs.append(stored)
                continue
            fallback = self._recompute_prepared_git_diff(session, segment.segment_id)
            if fallback is not None:
                segment_diffs.append(fallback)
            else:
                warnings.append(f"diff_unavailable:{segment.segment_id}")
        return _compose_diff(
            session,
            state="prepared",
            segment_diffs=tuple(segment_diffs),
            warnings=tuple(warnings),
        )

    def _recompute_prepared_git_diff(
        self,
        session: ChangeSession,
        segment_id: str,
    ) -> ChangeSegmentDiff | None:
        segment = next(
            (item for item in session.segments if item.segment_id == segment_id),
            None,
        )
        if segment is None or segment.kind is not ChangeSegmentKind.GIT_WORKTREE:
            return None
        detail = next(
            (item for item in session.git_details if item.worktree_path == segment.isolation_root),
            None,
        )
        if detail is None or detail.candidate_commit is None or self._worktrees is None:
            return None
        baseline = detail.baseline_commit or detail.base_sha
        try:
            unified, files = self._worktrees.prepared_diff(
                repository_root=Path(detail.repository_root),
                baseline_commit=baseline,
                candidate_commit=detail.candidate_commit,
            )
        except GitCommandFailedError:
            return None
        return ChangeSegmentDiff(
            segment_id=segment.segment_id,
            relative_root=segment.relative_root,
            unified_text=unified,
            files=files,
            candidate_digest=segment.candidate_digest,
        )

    def get_diff_stub(self, session_id: str) -> ChangeSessionDiff:
        """Compatibility alias for callers using the former stub method."""
        return self.get_diff(session_id)


def _compose_diff(
    session: ChangeSession,
    *,
    state: str,
    segment_diffs: tuple[ChangeSegmentDiff, ...],
    warnings: tuple[str, ...],
) -> ChangeSessionDiff:
    segment_id = (
        segment_diffs[0].segment_id
        if len(segment_diffs) == 1
        else ("composite" if len(session.segments) > 1 else session.segments[0].segment_id)
        if session.segments
        else ""
    )
    unified = "\n".join(item.unified_text for item in segment_diffs if item.unified_text)
    files = tuple(dict.fromkeys(path for item in segment_diffs for path in item.files))
    return ChangeSessionDiff(
        session_id=session.session_id,
        candidate_digest=session.candidate_digest,
        segment_id=segment_id,
        unified_text=unified,
        files=files,
        state=state,
        segments=segment_diffs,
        warnings=warnings,
    )
