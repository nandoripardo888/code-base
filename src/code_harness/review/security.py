"""Browser sessions for the loopback review server."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserSession:
    workspace_id: str
    csrf_token: str


class ReviewSecurity:
    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.Lock()

    def create_session(self, workspace_id: str) -> tuple[str, BrowserSession]:
        session_id = secrets.token_urlsafe(32)
        session = BrowserSession(
            workspace_id=workspace_id,
            csrf_token=secrets.token_urlsafe(32),
        )
        with self._lock:
            self._sessions[session_id] = session
        return session_id, session

    def get_session(self, session_id: str | None) -> BrowserSession | None:
        if session_id is None:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
