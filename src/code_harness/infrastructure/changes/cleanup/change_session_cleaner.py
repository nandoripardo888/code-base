from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from code_harness.domain.enums import ChangeSegmentKind, ChangeSessionStatus
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.protocols.blob_store import BlobStore
from code_harness.domain.protocols.change_session_store import ChangeSessionStore
from code_harness.infrastructure.changes.git.worktree_manager import LocalGitWorktreeManager


class ChangeSessionCleaner:
    def __init__(
        self,
        *,
        store: ChangeSessionStore,
        blob_store: BlobStore,
        sessions_home: Path,
        worktree_manager: LocalGitWorktreeManager | None = None,
    ) -> None:
        self._store = store
        self._blob_store = blob_store
        self._sessions_home = Path(sessions_home)
        self._worktrees = worktree_manager or LocalGitWorktreeManager()

    def cleanup_session(self, session: ChangeSession, *, preserve_conflict: bool = True) -> ChangeSession:
        if preserve_conflict and session.status is ChangeSessionStatus.CONFLICT:
            return session
        now = datetime.now(UTC).isoformat()
        self._store.update_status(session.session_id, ChangeSessionStatus.CLEANING, updated_at=now)
        self._store.append_event(
            ChangeSessionEvent(
                event_type="cleanup_started",
                occurred_at=now,
                session_id=session.session_id,
            )
        )
        for detail in session.git_details:
            self._worktrees.remove(
                repository_root=Path(detail.repository_root),
                worktree_path=Path(detail.worktree_path),
                temporary_branch=detail.temporary_branch,
                sessions_home=self._sessions_home,
            )
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="worktree_removed",
                    occurred_at=datetime.now(UTC).isoformat(),
                    session_id=session.session_id,
                    details={"worktree_path": detail.worktree_path},
                )
            )
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="branch_removed",
                    occurred_at=datetime.now(UTC).isoformat(),
                    session_id=session.session_id,
                    details={"temporary_branch": detail.temporary_branch},
                )
            )
        for segment in session.segments:
            if segment.kind is ChangeSegmentKind.WORKSPACE_MIRROR:
                isolation = Path(segment.isolation_root)
                if isolation.exists() and self._is_safe_session_path(isolation):
                    import shutil

                    shutil.rmtree(isolation, ignore_errors=True)
        released = self._blob_store.release_owner(session.session_id)
        if released:
            self._blob_store.gc()
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="blobs_released",
                    occurred_at=datetime.now(UTC).isoformat(),
                    session_id=session.session_id,
                    details={"released": released},
                )
            )
        session_dir = self._sessions_home / session.session_id
        if session_dir.exists() and self._is_safe_session_path(session_dir):
            import shutil

            shutil.rmtree(session_dir, ignore_errors=True)
        cleaned = self._store.update_status(
            session.session_id,
            ChangeSessionStatus.CLEANED,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._store.append_event(
            ChangeSessionEvent(
                event_type="session_cleaned",
                occurred_at=datetime.now(UTC).isoformat(),
                session_id=session.session_id,
            )
        )
        return cleaned

    def _is_safe_session_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve(strict=False)
            home = self._sessions_home.resolve(strict=False)
            resolved.relative_to(home)
            return resolved != home
        except ValueError:
            return False
