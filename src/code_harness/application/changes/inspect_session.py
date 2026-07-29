from __future__ import annotations

from pathlib import Path

from code_harness.domain.enums import ChangeSessionStatus
from code_harness.domain.errors import ChangeSessionInvalidStateError, ChangeSessionPathRejectedError
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionDiff, ChangeSessionEvent
from code_harness.domain.protocols.change_session_store import ChangeSessionStore


class InspectChangeSessionTool:
    def __init__(self, *, store: ChangeSessionStore) -> None:
        self._store = store

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

    def get_diff_stub(self, session_id: str) -> ChangeSessionDiff:
        """Return a lightweight diff descriptor after prepare (from events)."""
        session = self._store.get_session(session_id)
        segment_id = session.segments[0].segment_id if session.segments else ""
        return ChangeSessionDiff(
            session_id=session_id,
            candidate_digest=session.candidate_digest,
            segment_id=segment_id,
            unified_text="",
            files=(),
        )
