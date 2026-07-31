"""Short-lived tokens and browser sessions for the loopback review server."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserSession:
    transaction_id: str
    csrf_token: str


class ReviewSecurity:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.Lock()

    def issue_page_token(self, transaction_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = transaction_id
        return token

    def establish_session(self, page_token: str) -> tuple[str, BrowserSession] | None:
        with self._lock:
            transaction_id = self._tokens.pop(page_token, None)
            if transaction_id is None:
                return None
            session_id = secrets.token_urlsafe(32)
            session = BrowserSession(transaction_id, secrets.token_urlsafe(32))
            self._sessions[session_id] = session
            return session_id, session

    def get_session(self, session_id: str | None) -> BrowserSession | None:
        if session_id is None:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._sessions.clear()
