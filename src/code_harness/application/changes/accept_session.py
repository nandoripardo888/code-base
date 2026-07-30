from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from code_harness.domain.enums import ChangeSegmentKind, ChangeSessionStatus
from code_harness.domain.errors import (
    ChangeSessionConflictError,
    ChangeSessionDigestMismatchError,
    ChangeSessionInvalidStateError,
    ChangeSessionStaleError,
)
from code_harness.domain.models.change_segment import (
    ChangeSessionSegment,
    GitChangeSegment,
    MirrorChangeSegment,
)
from code_harness.domain.models.change_session import (
    ChangeSession,
    ChangeSessionEvent,
    IntegrationConflict,
    IntegrationFailure,
)
from code_harness.domain.protocols.change_integrator import ChangeIntegrator
from code_harness.domain.protocols.change_session_store import ChangeSessionStore

_ACCEPTABLE = frozenset(
    {
        ChangeSessionStatus.REVIEW_PENDING,
        ChangeSessionStatus.CONFLICT,
    }
)


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
        if session.status not in _ACCEPTABLE:
            raise ChangeSessionInvalidStateError(
                f"Cannot accept session in status {session.status.value!r}.",
                session_id=session_id,
                status=session.status.value,
            )
        if session.candidate_digest != candidate_digest:
            raise ChangeSessionDigestMismatchError(session_id)

        targets = _select_targets(session, segment_id)
        now = datetime.now(UTC).isoformat()
        retry = session.status is ChangeSessionStatus.CONFLICT
        if approval_id is not None and (not retry or session.approval_id is None):
            self._store.record_approval(
                approval_id=approval_id,
                session_id=session_id,
                candidate_digest=candidate_digest,
                state="consumed",
                created_at=now,
                decided_at=now,
            )

        session = _copy_session(
            session,
            status=ChangeSessionStatus.APPLYING,
            updated_at=now,
            segments=_reset_conflict_segments(session.segments, targets),
            approval_id=session.approval_id or approval_id,
            integration_failure=None,
        )
        self._store.save_session(session)
        self._store.append_event(
            ChangeSessionEvent(
                event_type="integration_started",
                occurred_at=now,
                session_id=session_id,
                details={"retry": retry},
            )
        )

        segment_results: dict[str, str] = {}
        for segment in targets:
            try:
                self._preflight(session, segment, segment_results)
            except ChangeSessionConflictError as error:
                return self._mark_conflict(
                    session,
                    segment,
                    error,
                    journal=segment_results,
                )
            except ChangeSessionStaleError as error:
                return self._mark_non_conflict_failure(
                    session,
                    segment,
                    ChangeSessionStatus.STALE,
                    error,
                    journal=segment_results,
                )

        for segment in targets:
            try:
                result = self._integrate(session, segment)
                segment_results[segment.segment_id] = result
                if result == "applied":
                    self._store.append_event(
                        ChangeSessionEvent(
                            event_type="segment_applied",
                            occurred_at=datetime.now(UTC).isoformat(),
                            session_id=session_id,
                            segment_id=segment.segment_id,
                            details={},
                        )
                    )
            except ChangeSessionConflictError as error:
                segment_results[segment.segment_id] = "conflict"
                return self._mark_conflict(
                    session,
                    segment,
                    error,
                    journal=segment_results,
                )
            except ChangeSessionStaleError as error:
                segment_results[segment.segment_id] = "stale"
                return self._mark_non_conflict_failure(
                    session,
                    segment,
                    ChangeSessionStatus.STALE,
                    error,
                    journal=segment_results,
                )
            except Exception as error:
                segment_results[segment.segment_id] = "failed"
                return self._mark_non_conflict_failure(
                    session,
                    segment,
                    ChangeSessionStatus.FAILED,
                    error,
                    journal=segment_results,
                )

        applied_at = datetime.now(UTC).isoformat()
        applied_segments = tuple(
            _segment_with_status(
                segment,
                ChangeSessionStatus.APPLIED.value,
            )
            if segment_results.get(segment.segment_id) == "applied"
            else segment
            for segment in session.segments
        )
        applied = _copy_session(
            session,
            status=ChangeSessionStatus.APPLIED,
            updated_at=applied_at,
            segments=applied_segments,
            integration_failure=None,
        )
        self._store.save_session(applied)
        self._store.append_event(
            ChangeSessionEvent(
                event_type="integration_completed",
                occurred_at=applied_at,
                session_id=session_id,
                details={"journal": segment_results},
            )
        )
        return self._cleaner.cleanup_session(applied, preserve_conflict=False)

    def _preflight(
        self,
        session: ChangeSession,
        segment: ChangeSessionSegment,
        results: dict[str, str],
    ) -> None:
        if segment.kind is ChangeSegmentKind.GIT_WORKTREE:
            git_detail = _git_detail(session, segment)
            if git_detail is None or git_detail.candidate_commit is None:
                results[segment.segment_id] = "unchanged"
                return
            if git_detail.integration_strategy == "workspace_patch_v2":
                proposed = self._store.list_proposed_files(
                    session.session_id,
                    segment.segment_id,
                )
                self._integrator.preflight_workspace_patch(git_detail, proposed)
            else:
                self._integrator.preflight_git(git_detail)
            return

        proposed = self._store.list_proposed_files(session.session_id, segment.segment_id)
        if not proposed:
            results[segment.segment_id] = "unchanged"
            return
        mirror_detail = _mirror_detail(session, segment)
        if mirror_detail is None:
            raise ChangeSessionInvalidStateError(
                "Missing mirror detail.",
                segment_id=segment.segment_id,
            )
        self._integrator.preflight_mirror(mirror_detail, proposed)

    def _integrate(
        self,
        session: ChangeSession,
        segment: ChangeSessionSegment,
    ) -> str:
        if segment.kind is ChangeSegmentKind.GIT_WORKTREE:
            git_detail = _git_detail(session, segment)
            if git_detail is None or git_detail.candidate_commit is None:
                return "unchanged"
            if git_detail.integration_strategy == "workspace_patch_v2":
                proposed = self._store.list_proposed_files(
                    session.session_id,
                    segment.segment_id,
                )
                self._integrator.integrate_workspace_patch(
                    git_detail,
                    proposed,
                    session_id=session.session_id,
                )
            else:
                self._integrator.integrate_git(git_detail)
            return "applied"

        proposed = self._store.list_proposed_files(session.session_id, segment.segment_id)
        if not proposed:
            return "unchanged"
        mirror_detail = _mirror_detail(session, segment)
        if mirror_detail is None:
            raise ChangeSessionInvalidStateError(
                "Missing mirror detail.",
                segment_id=segment.segment_id,
            )
        self._integrator.integrate_mirror(
            mirror_detail,
            proposed,
            session_id=session.session_id,
        )
        return "applied"

    def _mark_conflict(
        self,
        session: ChangeSession,
        segment: ChangeSessionSegment,
        error: ChangeSessionConflictError,
        *,
        journal: dict[str, str],
    ) -> ChangeSession:
        failure = _integration_failure(session, segment, error)
        failed_at = datetime.now(UTC).isoformat()
        segments = tuple(
            _segment_with_status(item, ChangeSessionStatus.CONFLICT.value)
            if item.segment_id == segment.segment_id
            else item
            for item in session.segments
        )
        conflicted = _copy_session(
            session,
            status=ChangeSessionStatus.CONFLICT,
            updated_at=failed_at,
            segments=segments,
            integration_failure=failure,
        )
        self._store.save_session(conflicted)
        details = _failure_details(failure)
        details["journal"] = dict(journal)
        self._store.append_event(
            ChangeSessionEvent(
                event_type="integration_failed",
                occurred_at=failed_at,
                session_id=session.session_id,
                segment_id=segment.segment_id,
                details=details,
            )
        )
        return self._store.get_session(session.session_id)

    def _mark_non_conflict_failure(
        self,
        session: ChangeSession,
        segment: ChangeSessionSegment,
        status: ChangeSessionStatus,
        error: Exception,
        *,
        journal: dict[str, str],
    ) -> ChangeSession:
        failed_at = datetime.now(UTC).isoformat()
        segments = tuple(
            _segment_with_status(item, status.value)
            if item.segment_id == segment.segment_id
            else item
            for item in session.segments
        )
        failed = _copy_session(
            session,
            status=status,
            updated_at=failed_at,
            segments=segments,
            integration_failure=None,
        )
        self._store.save_session(failed)
        self._store.append_event(
            ChangeSessionEvent(
                event_type="integration_failed",
                occurred_at=failed_at,
                session_id=session.session_id,
                segment_id=segment.segment_id,
                details={
                    "reason": status.value,
                    "message": str(error),
                    "journal": dict(journal),
                },
            )
        )
        return self._store.get_session(session.session_id)


def _select_targets(
    session: ChangeSession,
    segment_id: str | None,
) -> tuple[ChangeSessionSegment, ...]:
    if segment_id is None:
        return session.segments
    targets = tuple(item for item in session.segments if item.segment_id == segment_id)
    if not targets:
        raise ChangeSessionInvalidStateError(
            f"Unknown segment_id {segment_id!r}.",
            session_id=session.session_id,
            segment_id=segment_id,
        )
    return targets


def _git_detail(
    session: ChangeSession,
    segment: ChangeSessionSegment,
) -> GitChangeSegment | None:
    return next(
        (item for item in session.git_details if item.worktree_path == segment.isolation_root),
        None,
    )


def _mirror_detail(
    session: ChangeSession,
    segment: ChangeSessionSegment,
) -> MirrorChangeSegment | None:
    return next(
        (item for item in session.mirror_details if item.mirror_root == segment.isolation_root),
        None,
    )


def _reset_conflict_segments(
    segments: tuple[ChangeSessionSegment, ...],
    targets: tuple[ChangeSessionSegment, ...],
) -> tuple[ChangeSessionSegment, ...]:
    target_ids = {item.segment_id for item in targets}
    return tuple(
        _segment_with_status(item, ChangeSessionStatus.REVIEW_PENDING.value)
        if item.segment_id in target_ids and item.status == ChangeSessionStatus.CONFLICT.value
        else item
        for item in segments
    )


def _segment_with_status(
    segment: ChangeSessionSegment,
    status: str,
) -> ChangeSessionSegment:
    return ChangeSessionSegment(
        segment_id=segment.segment_id,
        kind=segment.kind,
        relative_root=segment.relative_root,
        source_root=segment.source_root,
        isolation_root=segment.isolation_root,
        status=status,
        base_digest=segment.base_digest,
        candidate_digest=segment.candidate_digest,
    )


def _integration_failure(
    session: ChangeSession,
    segment: ChangeSessionSegment,
    error: ChangeSessionConflictError,
) -> IntegrationFailure:
    detail = _git_detail(session, segment)
    strategy = detail.integration_strategy if detail is not None else "workspace_mirror"
    conflicts: list[IntegrationConflict] = []
    raw_conflicts = error.details.get("conflicts", ())
    if isinstance(raw_conflicts, (tuple, list)):
        for raw in raw_conflicts:
            if not isinstance(raw, dict):
                continue
            path = raw.get("path")
            kind = raw.get("kind")
            if not isinstance(path, str) or not isinstance(kind, str):
                continue
            conflicts.append(
                IntegrationConflict(
                    path=path,
                    kind=kind,
                    base_sha256=_optional_string(raw.get("base_sha256")),
                    current_sha256=_optional_string(raw.get("current_sha256")),
                    proposed_sha256=_optional_string(raw.get("proposed_sha256")),
                )
            )
    return IntegrationFailure(
        code=error.code.value,
        message=error.message,
        failed_segment=segment.segment_id,
        strategy=strategy,
        conflicts=tuple(conflicts),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _failure_details(failure: IntegrationFailure) -> dict[str, object]:
    return {
        "reason": "conflict",
        "code": failure.code,
        "message": failure.message,
        "failed_segment": failure.failed_segment,
        "strategy": failure.strategy,
        "conflicts": [
            {
                "path": item.path,
                "kind": item.kind,
                "base_sha256": item.base_sha256,
                "current_sha256": item.current_sha256,
                "proposed_sha256": item.proposed_sha256,
            }
            for item in failure.conflicts
        ],
        "available_actions": ["retry_accept", "reject"],
    }


def _copy_session(
    session: ChangeSession,
    *,
    status: ChangeSessionStatus,
    updated_at: str,
    segments: tuple[ChangeSessionSegment, ...] | None = None,
    approval_id: str | None = None,
    integration_failure: IntegrationFailure | None,
) -> ChangeSession:
    return ChangeSession(
        session_id=session.session_id,
        workspace_id=session.workspace_id,
        workspace_root=session.workspace_root,
        topology_kind=session.topology_kind,
        status=status,
        created_at=session.created_at,
        updated_at=updated_at,
        expires_at=session.expires_at,
        segments=segments if segments is not None else session.segments,
        candidate_digest=session.candidate_digest,
        approval_id=approval_id if approval_id is not None else session.approval_id,
        warnings=session.warnings,
        git_details=session.git_details,
        mirror_details=session.mirror_details,
        integration_failure=integration_failure,
    )
