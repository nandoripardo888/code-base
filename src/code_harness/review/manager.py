"""Lazy lifecycle manager for the loopback-only review server."""

from __future__ import annotations

import threading
import webbrowser
from http.server import ThreadingHTTPServer

from code_harness.history import HistoryManager
from code_harness.review.security import ReviewSecurity
from code_harness.review.server import make_request_handler
from code_harness.review.service import ReviewService


class _ReviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class ReviewManager:
    """Starts one local server per Session and shuts it down with that Session."""

    def __init__(self, history: HistoryManager) -> None:
        self.service = ReviewService(history)
        self.security = ReviewSecurity()
        self._server: _ReviewHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._origin: str | None = None
        self._lock = threading.Lock()

    @property
    def origin(self) -> str:
        self._ensure_started()
        if self._origin is None:
            raise RuntimeError("Review server did not publish its local origin.")
        return self._origin

    def open(
        self,
        transaction_id: str = "latest",
        *,
        open_browser: bool = False,
    ) -> dict[str, object]:
        resolved = self.service.resolve_transaction_id(transaction_id)
        summary = self.service.get_summary(resolved)
        token = self.security.issue_page_token(self.service.history.workspace_id, resolved)
        url = f"{self.origin}/r/{token}"
        opened = False
        if open_browser:
            try:
                opened = bool(webbrowser.open_new_tab(url))
            except webbrowser.Error:
                opened = False
        return {
            "available": True,
            "files": summary.files_changed,
            "additions": summary.additions,
            "deletions": summary.deletions,
            "url": url,
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

    def _ensure_started(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            handler = make_request_handler(self)
            server = _ReviewHTTPServer(("127.0.0.1", 0), handler)
            raw_host, port = server.server_address[:2]
            host = raw_host.decode() if isinstance(raw_host, bytes) else raw_host
            self._origin = f"http://{host}:{port}"
            self._server = server
            thread = threading.Thread(
                target=server.serve_forever,
                name="code-harness-review",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def __enter__(self) -> ReviewManager:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        self.shutdown()
