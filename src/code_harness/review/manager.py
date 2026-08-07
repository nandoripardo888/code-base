"""Lifecycle manager for the loopback-only review portal."""

from __future__ import annotations

import os
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

from code_harness.history import HistoryManager
from code_harness.review.security import ReviewSecurity
from code_harness.review.server import make_request_handler
from code_harness.review.service import ReviewService

DEFAULT_REVIEW_PORT = 8765


def resolve_review_port(port: int | None = None) -> int:
    """Resolve the review listen port: argument, env, then default."""
    if port is not None:
        return port
    raw = os.environ.get("CODE_HARNESS_REVIEW_PORT", str(DEFAULT_REVIEW_PORT)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(
            f"CODE_HARNESS_REVIEW_PORT must be an integer, got {raw!r}."
        ) from error
    if value < 0 or value > 65535:
        raise ValueError(f"CODE_HARNESS_REVIEW_PORT out of range: {value}.")
    return value


class _ReviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ReviewManager:
    """Starts one local portal per Session on a fixed loopback port."""

    def __init__(self, history: HistoryManager, *, port: int | None = None) -> None:
        self.service = ReviewService(history)
        self.security = ReviewSecurity()
        self._requested_port = resolve_review_port(port)
        self._server: _ReviewHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._origin: str | None = None
        self._lock = threading.Lock()

    @property
    def origin(self) -> str:
        self.ensure_started()
        if self._origin is None:
            raise RuntimeError("Review server did not publish its local origin.")
        return self._origin

    @property
    def port(self) -> int:
        self.ensure_started()
        assert self._server is not None
        return int(self._server.server_address[1])

    def ensure_started(self) -> str:
        """Bind the portal if needed and return its origin URL."""
        with self._lock:
            if self._server is not None:
                assert self._origin is not None
                return self._origin
            handler = make_request_handler(self)
            try:
                server = _ReviewHTTPServer(("127.0.0.1", self._requested_port), handler)
            except OSError as error:
                requested = self._requested_port
                hint = (
                    f"Review portal could not bind 127.0.0.1:{requested}. "
                    "Stop the other process using that port or set "
                    "CODE_HARNESS_REVIEW_PORT to a free port."
                )
                raise RuntimeError(hint) from error
            raw_host, bound_port = server.server_address[:2]
            host = raw_host.decode() if isinstance(raw_host, bytes) else raw_host
            self._origin = f"http://{host}:{bound_port}"
            self._server = server
            thread = threading.Thread(
                target=server.serve_forever,
                name="code-harness-review",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return self._origin

    def open(
        self,
        identifier: str = "latest",
        *,
        transaction_id: str | None = None,
        open_browser: bool = False,
    ) -> dict[str, object]:
        selection = self.service.resolve_selection(identifier, transaction_id=transaction_id)
        summary = self.service.get_patch(selection.group_id, selection.transaction_id)
        query = urlencode(
            {
                "group": selection.group_id,
                "patch": selection.transaction_id,
            }
        )
        url = f"{self.origin}/?{query}"
        opened = False
        if open_browser:
            try:
                opened = bool(webbrowser.open_new_tab(url))
            except webbrowser.Error:
                opened = False
        return {
            "available": True,
            "group_id": selection.group_id,
            "transaction_id": selection.transaction_id,
            "files": summary.files_changed,
            "additions": summary.additions,
            "deletions": summary.deletions,
            "url": url,
            "origin": self.origin,
            "browser_opened": opened,
        }

    def shutdown(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._origin = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self.security.clear()

    def __enter__(self) -> ReviewManager:
        self.ensure_started()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        self.shutdown()
