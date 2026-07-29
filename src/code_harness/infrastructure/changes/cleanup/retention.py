from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from code_harness.domain.enums import ChangeSessionStatus
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.protocols.blob_store import BlobStore
from code_harness.domain.protocols.change_session_store import ChangeSessionStore
from code_harness.infrastructure.changes.cleanup.change_session_cleaner import ChangeSessionCleaner


class ChangeSessionRetentionCleaner:
    def __init__(
        self,
        *,
        store: ChangeSessionStore,
        cleaner: ChangeSessionCleaner,
        blob_store: BlobStore,
        conflict_retention_hours: int = 168,
        abandoned_retention_hours: int = 24,
        failed_preparation_retention_hours: int = 1,
        audit_retention_days: int = 30,
    ) -> None:
        self._store = store
        self._cleaner = cleaner
        self._blob_store = blob_store
        self._conflict_hours = conflict_retention_hours
        self._abandoned_hours = abandoned_retention_hours
        self._failed_prep_hours = failed_preparation_retention_hours
        self._audit_days = audit_retention_days

    def run(self, *, dry_run: bool = False, expired_only: bool = False) -> dict[str, object]:
        started = datetime.now(UTC)
        now = started
        actions: list[dict[str, object]] = []
        sessions = self._store.list_sessions(limit=500)
        for session in sessions:
            action = self._classify(session, now)
            if action is None:
                continue
            if expired_only and action != "expired_cleanup":
                continue
            entry = {
                "session_id": session.session_id,
                "action": action,
                "status": session.status.value,
            }
            if not dry_run:
                if action in {
                    "immediate_cleanup",
                    "expired_cleanup",
                    "abandoned_cleanup",
                    "conflict_expired_cleanup",
                }:
                    self._cleaner.cleanup_session(session, preserve_conflict=False)
            actions.append(entry)
        removed_blobs = 0 if dry_run else self._blob_store.gc()
        finished = datetime.now(UTC)
        details = {
            "actions": actions,
            "removed_blobs": removed_blobs,
            "dry_run": dry_run,
        }
        self._store.record_cleanup_run(
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            dry_run=dry_run,
            details=details,
        )
        return details

    def _classify(self, session: ChangeSession, now: datetime) -> str | None:
        updated = _parse_ts(session.updated_at)
        age = now - updated
        if session.status in {
            ChangeSessionStatus.APPLIED,
            ChangeSessionStatus.REJECTED,
        }:
            return "immediate_cleanup"
        if session.status is ChangeSessionStatus.CLEANED:
            return None
        if session.status is ChangeSessionStatus.CONFLICT:
            if age >= timedelta(hours=self._conflict_hours):
                return "conflict_expired_cleanup"
            return None
        if session.status is ChangeSessionStatus.FAILED:
            if age >= timedelta(hours=self._conflict_hours):
                return "expired_cleanup"
            return None
        if session.status is ChangeSessionStatus.PREPARING:
            if age >= timedelta(hours=self._failed_prep_hours):
                return "abandoned_cleanup"
            return None
        if session.status in {
            ChangeSessionStatus.READY,
            ChangeSessionStatus.AGENT_WORKING,
            ChangeSessionStatus.REVIEW_PENDING,
        }:
            if age >= timedelta(hours=self._abandoned_hours):
                return "abandoned_cleanup"
            return None
        if session.status is ChangeSessionStatus.EXPIRED:
            return "expired_cleanup"
        return None


class ChangeSessionRecovery:
    def __init__(
        self,
        *,
        store: ChangeSessionStore,
        cleaner: ChangeSessionCleaner,
        sessions_home: Path,
    ) -> None:
        self._store = store
        self._cleaner = cleaner
        self._sessions_home = Path(sessions_home)

    def recover_all(self) -> tuple[ChangeSession, ...]:
        recovered: list[ChangeSession] = []
        for session in self._store.list_non_terminal():
            recovered.append(self.recover(session.session_id))
        return tuple(recovered)

    def recover(self, session_id: str) -> ChangeSession:
        session = self._store.get_session(session_id)
        now = datetime.now(UTC).isoformat()
        if session.status is ChangeSessionStatus.APPLYING:
            updated = self._store.update_status(
                session_id,
                ChangeSessionStatus.FAILED,
                updated_at=now,
                warnings=tuple(session.warnings)
                + ("Recovered interrupted apply; manual review required.",),
            )
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="integration_failed",
                    occurred_at=now,
                    session_id=session_id,
                    details={"reason": "recovered_interrupted_apply"},
                )
            )
            return updated
        if session.status is ChangeSessionStatus.PREPARING:
            has_resources = any(
                Path(item.worktree_path).exists() for item in session.git_details
            ) or any(Path(item.mirror_root).exists() for item in session.mirror_details)
            if not has_resources:
                return self._cleaner.cleanup_session(session, preserve_conflict=False)
            return self._store.update_status(
                session_id,
                ChangeSessionStatus.FAILED,
                updated_at=now,
                warnings=tuple(session.warnings) + ("Recovered incomplete preparation.",),
            )
        if session.status is ChangeSessionStatus.AGENT_WORKING:
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="agent_started",
                    occurred_at=now,
                    session_id=session_id,
                    details={"recovered": True},
                )
            )
            return session
        if session.status is ChangeSessionStatus.CLEANING:
            return self._cleaner.cleanup_session(session, preserve_conflict=False)
        return session


def _parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
