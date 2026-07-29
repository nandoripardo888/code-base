from __future__ import annotations

from datetime import UTC, datetime

from code_harness.application.changes.accept_session import ChangeSessionCleanerPort
from code_harness.domain.enums import ChangeSessionStatus
from code_harness.domain.errors import ChangeSessionInvalidStateError
from code_harness.domain.models.change_session import ChangeSession, ChangeSessionEvent
from code_harness.domain.protocols.change_session_store import ChangeSessionStore

_REJECTABLE = frozenset(
    {
        ChangeSessionStatus.READY,
        ChangeSessionStatus.AGENT_WORKING,
        ChangeSessionStatus.REVIEW_PENDING,
        ChangeSessionStatus.CONFLICT,
        ChangeSessionStatus.FAILED,
        ChangeSessionStatus.STALE,
    }
)


class RejectChangeSessionTool:
    def __init__(
        self,
        *,
        store: ChangeSessionStore,
        cleaner: ChangeSessionCleanerPort,
    ) -> None:
        self._store = store
        self._cleaner = cleaner

    def run(self, session_id: str, *, reason: str | None = None) -> ChangeSession:
        session = self._store.get_session(session_id)
        if session.status not in _REJECTABLE:
            raise ChangeSessionInvalidStateError(
                f"Cannot reject session in status {session.status.value!r}.",
                session_id=session_id,
                status=session.status.value,
            )
        now = datetime.now(UTC).isoformat()
        self._store.update_status(
            session_id,
            ChangeSessionStatus.REJECTED,
            updated_at=now,
        )
        self._store.append_event(
            ChangeSessionEvent(
                event_type="session_rejected",
                occurred_at=now,
                session_id=session_id,
                details={"reason": reason} if reason else {},
            )
        )
        rejected = self._store.get_session(session_id)
        return self._cleaner.cleanup_session(rejected, preserve_conflict=False)
