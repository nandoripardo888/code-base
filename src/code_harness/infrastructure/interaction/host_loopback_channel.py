"""Local loopback channel for interactive human approvals."""

from __future__ import annotations

import html
import logging
import secrets
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from code_harness.domain.enums import ApprovalDecisionSource, HumanDecisionOutcome
from code_harness.domain.models.human_decision import HumanDecisionRequest, HumanDecisionResult
from code_harness.infrastructure.interaction.approval_decision_broker import (
    ApprovalDecisionBroker,
)

_LOGGER = logging.getLogger(__name__)
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    # "no-referrer" makes browsers serialize the Origin of the approval form POST
    # as "null", which the origin check must then reject.
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}
_MAX_BODY_BYTES = 4_096


@dataclass(slots=True)
class _PendingApproval:
    request: HumanDecisionRequest
    csrf_token: str


class HostLoopbackDecisionChannel:
    """HumanDecisionChannel served on an exclusive 127.0.0.1 HTTP listener."""

    def __init__(
        self,
        *,
        port: int | None = None,
        open_browser: bool = True,
    ) -> None:
        self._port = 0 if port is None else port
        self._open_browser = open_browser
        self._broker = ApprovalDecisionBroker()
        self._pending: dict[str, _PendingApproval] = {}
        self._pending_guard = threading.Lock()
        self._opened: set[str] = set()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._bound_port: int | None = None
        self._shutdown_done = False
        self._start_server()

    @property
    def source(self) -> str:
        return ApprovalDecisionSource.HOST_LOOPBACK.value

    @property
    def base_url(self) -> str:
        port = self._bound_port or self._port
        return f"http://127.0.0.1:{port}"

    @property
    def bound_port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("Host loopback server is not running.")
        return self._bound_port

    async def request_decision(
        self,
        request: HumanDecisionRequest,
        *,
        timeout_seconds: float,
    ) -> HumanDecisionResult:
        csrf_token = secrets.token_urlsafe(32)
        with self._pending_guard:
            self._pending[request.request_id] = _PendingApproval(request, csrf_token)
        waiter = await self._broker.register(request.request_id)
        try:
            # Double-check: a concurrent POST may have arrived before register.
            with self._pending_guard:
                still_pending = request.request_id in self._pending
            if not still_pending and waiter.result is not None:
                return waiter.result
            self._ensure_browser_open(request.request_id)
            decided = await waiter.wait(timeout_seconds=timeout_seconds)
            if decided is None:
                return HumanDecisionResult(
                    outcome=HumanDecisionOutcome.TIMED_OUT,
                    source=self.source,
                )
            return decided
        finally:
            await self._broker.unregister(request.request_id)
            with self._pending_guard:
                self._pending.pop(request.request_id, None)
                self._opened.discard(request.request_id)

    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._broker.shutdown()
        with self._pending_guard:
            self._pending.clear()
            self._opened.clear()
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def _start_server(self) -> None:
        channel = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                _LOGGER.debug("host_loopback: " + format, *args)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/pending":
                    self._write_pending()
                    return
                approval_id = self._approval_id(parsed.path)
                if approval_id is None:
                    self._write_text(404, "Not found")
                    return
                if not self._validate_host():
                    self._write_text(400, "Invalid Host")
                    return
                pending = channel._get_pending(approval_id)
                if pending is None:
                    self._write_text(404, "Approval is no longer pending")
                    return
                self._write_html(200, channel._render_form(pending))

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                approval_id = self._approval_id(parsed.path)
                if approval_id is None:
                    self._reject_post(404, "Not found")
                    return
                if not self._validate_host():
                    self._reject_post(400, "Invalid Host")
                    return
                if not self._validate_origin():
                    self._reject_post(403, "Invalid Origin")
                    return
                content_type = self.headers.get("Content-Type", "")
                if not content_type.startswith("application/x-www-form-urlencoded"):
                    self._reject_post(415, "Unsupported Content-Type")
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > _MAX_BODY_BYTES:
                    self._write_text(413, "Invalid body size")
                    return
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                form = parse_qs(body, keep_blank_values=False)
                token = (form.get("csrf_token") or [""])[0]
                decision = (form.get("decision") or [""])[0]
                pending = channel._get_pending(approval_id)
                if pending is None:
                    self._write_text(404, "Approval is no longer pending")
                    return
                if not secrets.compare_digest(token, pending.csrf_token):
                    self._write_text(403, "Invalid CSRF token")
                    return
                if decision not in {"approve", "deny"}:
                    self._write_text(400, "Invalid decision")
                    return
                outcome = (
                    HumanDecisionOutcome.APPROVED
                    if decision == "approve"
                    else HumanDecisionOutcome.DENIED
                )
                result = HumanDecisionResult(outcome=outcome, source=channel.source)
                published = channel._broker.publish(approval_id, result)
                with channel._pending_guard:
                    channel._pending.pop(approval_id, None)
                if not published:
                    self._write_text(409, "No waiter registered")
                    return
                label = "approved" if outcome is HumanDecisionOutcome.APPROVED else "denied"
                self._write_html(
                    200,
                    channel._render_done(pending.request.title, label),
                )

            def _approval_id(self, path: str) -> str | None:
                prefix = "/approvals/"
                if not path.startswith(prefix):
                    return None
                value = path[len(prefix) :].strip("/")
                return value or None

            def _validate_host(self) -> bool:
                host = self.headers.get("Host", "")
                expected = {
                    f"127.0.0.1:{channel._bound_port}",
                    f"localhost:{channel._bound_port}",
                }
                return host in expected

            def _validate_origin(self) -> bool:
                origin = self.headers.get("Origin")
                if origin is None:
                    return True
                if origin in {
                    f"http://127.0.0.1:{channel._bound_port}",
                    f"http://localhost:{channel._bound_port}",
                }:
                    return True
                # An opaque origin is accepted only when fetch metadata proves the
                # POST did not leave the approval page.
                return origin == "null" and self.headers.get("Sec-Fetch-Site") == "same-origin"

            def _drain_body(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                remaining = min(max(length, 0), _MAX_BODY_BYTES)
                if remaining:
                    self.rfile.read(remaining)

            def _reject_post(self, status: int, message: str) -> None:
                self._drain_body()
                self._write_text(status, message)

            def _write_pending(self) -> None:
                if not self._validate_host():
                    self._write_text(400, "Invalid Host")
                    return
                with channel._pending_guard:
                    items = tuple(channel._pending.values())
                rows = "".join(
                    (
                        "<li><a href="
                        f'"/approvals/{html.escape(item.request.request_id)}">'
                        f"{html.escape(item.request.title)}</a> "
                        f"({html.escape(item.request.subject_kind)})</li>"
                    )
                    for item in items
                )
                body = (
                    "<!doctype html><html><head><title>Pending approvals</title>"
                    f"{channel._style()}</head><body>"
                    "<h1>Pending approvals</h1>"
                    f"<ul>{rows or '<li>None</li>'}</ul>"
                    "</body></html>"
                )
                self._write_html(200, body)

            def _write_html(self, status: int, body: str) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in _SECURITY_HEADERS.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

            def _write_text(self, status: int, message: str) -> None:
                payload = message.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in _SECURITY_HEADERS.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", self._port), Handler)
        self._server = server
        self._bound_port = int(server.server_address[1])
        thread = threading.Thread(
            target=server.serve_forever,
            name="code-harness-host-loopback",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _get_pending(self, approval_id: str) -> _PendingApproval | None:
        with self._pending_guard:
            return self._pending.get(approval_id)

    def _ensure_browser_open(self, approval_id: str) -> None:
        url = f"{self.base_url}/approvals/{approval_id}"
        with self._pending_guard:
            if approval_id in self._opened:
                return
            self._opened.add(approval_id)
        print(f"code-harness approval required: {url}", file=sys.stderr, flush=True)
        if not self._open_browser:
            return
        try:
            webbrowser.open(url, new=2, autoraise=True)
        except Exception:
            _LOGGER.info("Unable to open browser for host loopback approval.", exc_info=True)

    @staticmethod
    def _style() -> str:
        return (
            "<style>"
            "body{font-family:Segoe UI,sans-serif;margin:2rem;max-width:42rem;}"
            "pre{white-space:pre-wrap;background:#f6f8fa;padding:1rem;}"
            "button{margin-right:.5rem;padding:.5rem 1rem;}"
            "</style>"
        )

    def _render_form(self, pending: _PendingApproval) -> str:
        request = pending.request
        details = "".join(
            (
                f"<tr><th>{html.escape(item.label)}</th>"
                f"<td>{html.escape(item.value)}</td></tr>"
            )
            for item in request.details
        )
        risks = "".join(
            f"<li>{html.escape(item.severity.value)}: {html.escape(item.message)}</li>"
            for item in request.risks
        ) or "<li>none reported</li>"
        return (
            "<!doctype html><html><head>"
            f"<title>{html.escape(request.title)}</title>{self._style()}"
            "</head><body>"
            f"<h1>{html.escape(request.title)}</h1>"
            f"<p>{html.escape(request.subject_kind)} - "
            f"expires {html.escape(request.expires_at)}</p>"
            f"<table>{details}</table>"
            f"<h2>Risks</h2><ul>{risks}</ul>"
            f"<pre>{html.escape(request.summary)}</pre>"
            f'<form method="post" action="/approvals/{html.escape(request.request_id)}">'
            f'<input type="hidden" name="csrf_token" value="{html.escape(pending.csrf_token)}">'
            '<button type="submit" name="decision" value="approve">Approve once</button>'
            '<button type="submit" name="decision" value="deny">Deny</button>'
            "</form></body></html>"
        )

    def _render_done(self, title: str, label: str) -> str:
        return (
            "<!doctype html><html><head>"
            f"<title>{html.escape(title)}</title>{self._style()}"
            "</head><body>"
            f"<h1>{html.escape(title)}</h1>"
            f"<p>Decision recorded: <strong>{html.escape(label)}</strong>.</p>"
            "<p>You can close this window.</p>"
            "</body></html>"
        )
