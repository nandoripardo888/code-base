from __future__ import annotations

from datetime import UTC, datetime

from code_harness.domain.enums import ChangeSegmentKind, ChangeSessionStatus
from code_harness.domain.errors import (
    ChangeSessionConflictError,
    ChangeSessionDigestMismatchError,
    ChangeSessionInvalidStateError,
    ChangeSessionStaleError,
)
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.protocols.change_integrator import ChangeIntegrator
from code_harness.domain.protocols.change_session_store import ChangeSessionStore
from typing import Protocol


class ChangeSessionCleanerPort(Protocol):
    def cleanup_session(
        self,
        session: ChangeSession,
        *,
        preserve_conflict: bool = True,
    ) -> ChangeSession: ...


class AcceptChangeSessionTool:
    def __init__(
        self,
        *,
        store: ChangeSessionStore,
        cleaner: ChangeSessionCleanerPort,
        integrator: ChangeIntegrator,
    ) -> None:
        self._store = store
        self._cleaner = cleaner
        self._integrator = integrator

    def run(
        self,
        session_id: str,
        *,
        candidate_digest: str,
        segment_id: str | None = None,
        approval_id: str | None = None,
    ) -> ChangeSession:
        session = self._store.get_session(session_id)
        if session.status is not ChangeSessionStatus.REVIEW_PENDING:
            raise ChangeSessionInvalidStateError(
                f"Cannot accept session in status {session.status.value!r}.",
                session_id=session_id,
                status=session.status.value,
            )
        if session.candidate_digest != candidate_digest:
            raise ChangeSessionDigestMismatchError(session_id)

        now = datetime.now(UTC).isoformat()
        if approval_id is not None:
            self._store.record_approval(
                approval_id=approval_id,
                session_id=session_id,
                candidate_digest=candidate_digest,
                state="consumed",
                created_at=now,
                decided_at=now,
            )
            session = self._store.update_status(
                session_id,
                ChangeSessionStatus.REVIEW_PENDING,
                updated_at=now,
                approval_id=approval_id,
            )

        self._store.update_status(session_id, ChangeSessionStatus.APPLYING, updated_at=now)
        self._store.append_event(
            ChangeSessionEvent(
                event_type="integration_started",
                occurred_at=now,
                session_id=session_id,
            )
        )

        session = self._store.get_session(session_id)
        segment_results: dict[str, str] = {}
        targets = session.segments
        if segment_id is not None:
            targets = tuple(item for item in session.segments if item.segment_id == segment_id)

        # Global preflight first.
        try:
            for segment in targets:
                if segment.kind is ChangeSegmentKind.GIT_WORKTREE:
                    detail = next(
                        (
                            item
                            for item in session.git_details
                            if item.worktree_path == segment.isolation_root
                        ),
                        None,
                    )
                    if detail is None or detail.candidate_commit is None:
                        segment_results[segment.segment_id] = "unchanged"
                        continue
                    self._integrator.preflight_git(detail)
                else:
                    proposed = self._store.list_proposed_files(session_id, segment.segment_id)
                    if not proposed:
                        segment_results[segment.segment_id] = "unchanged"
                        continue
                    detail = next(
                        (
                            item
                            for item in session.mirror_details
                            if item.mirror_root == segment.isolation_root
                        ),
                        None,
                    )
                    if detail is None:
                        raise ChangeSessionInvalidStateError(
                            "Missing mirror detail.",
                            segment_id=segment.segment_id,
                        )
                    self._integrator.preflight_mirror(detail, proposed)
        except ChangeSessionStaleError as error:
            self._store.update_status(
                session_id,
                ChangeSessionStatus.STALE,
                updated_at=datetime.now(UTC).isoformat(),
            )
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="integration_failed",
                    occurred_at=datetime.now(UTC).isoformat(),
                    session_id=session_id,
                    details={"reason": "stale", "message": str(error)},
                )
            )
            return self._store.get_session(session_id)
        except ChangeSessionConflictError as error:
            self._store.update_status(
                session_id,
                ChangeSessionStatus.CONFLICT,
                updated_at=datetime.now(UTC).isoformat(),
            )
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="integration_failed",
                    occurred_at=datetime.now(UTC).isoformat(),
                    session_id=session_id,
                    details={"reason": "conflict", "message": str(error)},
                )
            )
            return self._store.get_session(session_id)

        failed = False
        for segment in targets:
            if segment.kind is ChangeSegmentKind.GIT_WORKTREE:
                detail = next(
                    (
                        item
                        for item in session.git_details
                        if item.worktree_path == segment.isolation_root
                    ),
                    None,
                )
                if detail is None or detail.candidate_commit is None:
                    segment_results.setdefault(segment.segment_id, "unchanged")
                    continue
                try:
                    head = self._integrator.integrate_git(detail)
                    segment_results[segment.segment_id] = "applied"
                    self._store.append_event(
                        ChangeSessionEvent(
                            event_type="segment_applied",
                            occurred_at=datetime.now(UTC).isoformat(),
                            session_id=session_id,
                            segment_id=segment.segment_id,
                            details={"head": head},
                        )
                    )
                except ChangeSessionConflictError:
                    segment_results[segment.segment_id] = "conflict"
                    failed = True
                    self._store.append_event(
                        ChangeSessionEvent(
                            event_type="integration_failed",
                            occurred_at=datetime.now(UTC).isoformat(),
                            session_id=session_id,
                            segment_id=segment.segment_id,
                            details={"reason": "conflict", "journal": segment_results},
                        )
                    )
                    break
                except ChangeSessionStaleError:
                    segment_results[segment.segment_id] = "stale"
                    failed = True
                    break
            else:
                proposed = self._store.list_proposed_files(session_id, segment.segment_id)
                if not proposed:
                    segment_results.setdefault(segment.segment_id, "unchanged")
                    continue
                detail = next(
                    (
                        item
                        for item in session.mirror_details
                        if item.mirror_root == segment.isolation_root
                    ),
                    None,
                )
                assert detail is not None
                try:
                    self._integrator.integrate_mirror(
                        detail,
                        proposed,
                        session_id=session_id,
                    )
                    segment_results[segment.segment_id] = "applied"
                    self._store.append_event(
                        ChangeSessionEvent(
                            event_type="segment_applied",
                            occurred_at=datetime.now(UTC).isoformat(),
                            session_id=session_id,
                            segment_id=segment.segment_id,
                            details={},
                        )
                    )
                except ChangeSessionStaleError:
                    segment_results[segment.segment_id] = "stale"
                    failed = True
                    self._store.append_event(
                        ChangeSessionEvent(
                            event_type="integration_failed",
                            occurred_at=datetime.now(UTC).isoformat(),
                            session_id=session_id,
                            segment_id=segment.segment_id,
                            details={"reason": "stale", "journal": segment_results},
                        )
                    )
                    break
                except Exception:
                    segment_results[segment.segment_id] = "failed"
                    failed = True
                    self._store.update_status(
                        session_id,
                        ChangeSessionStatus.FAILED,
                        updated_at=datetime.now(UTC).isoformat(),
                    )
                    self._store.append_event(
                        ChangeSessionEvent(
                            event_type="integration_failed",
                            occurred_at=datetime.now(UTC).isoformat(),
                            session_id=session_id,
                            segment_id=segment.segment_id,
                            details={"reason": "failed", "journal": segment_results},
                        )
                    )
                    return self._store.get_session(session_id)

        if failed:
            status = (
                ChangeSessionStatus.CONFLICT
                if "conflict" in segment_results.values()
                else ChangeSessionStatus.STALE
            )
            self._store.update_status(
                session_id,
                status,
                updated_at=datetime.now(UTC).isoformat(),
            )
            self._store.append_event(
                ChangeSessionEvent(
                    event_type="integration_failed",
                    occurred_at=datetime.now(UTC).isoformat(),
                    session_id=session_id,
                    details={"journal": segment_results},
                )
            )
            return self._store.get_session(session_id)

        applied_at = datetime.now(UTC).isoformat()
        self._store.update_status(
            session_id,
            ChangeSessionStatus.APPLIED,
            updated_at=applied_at,
        )
        self._store.append_event(
            ChangeSessionEvent(
                event_type="segment_applied",
                occurred_at=applied_at,
                session_id=session_id,
                details={"journal": segment_results},
            )
        )
        applied = self._store.get_session(session_id)
        return self._cleaner.cleanup_session(applied, preserve_conflict=False)
