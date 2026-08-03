"""Hardened standard-library HTTP adapter for the review service."""

from __future__ import annotations

import html
import json
import re
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from code_harness.errors import HarnessError, PatchRollbackConflictError

if TYPE_CHECKING:
    from code_harness.review.manager import ReviewManager
    from code_harness.review.security import BrowserSession

_STATIC_ROOT = Path(__file__).with_name("static")
_ASSETS = {
    "/review.css": ("review.css", "text/css; charset=utf-8"),
    "/review.js": ("review.js", "text/javascript; charset=utf-8"),
    "/static/review.css": ("review.css", "text/css; charset=utf-8"),
    "/static/review.js": ("review.js", "text/javascript; charset=utf-8"),
}
_GROUP_API = re.compile(r"^/api/groups/([A-Za-z0-9_-]+)$")
_PATCH_API = re.compile(
    r"^/api/groups/([A-Za-z0-9_-]+)/patches/([A-Za-z0-9_-]+)$"
)
_FILE_API = re.compile(
    r"^/api/groups/([A-Za-z0-9_-]+)/patches/([A-Za-z0-9_-]+)/files/(\d+)$"
)
_PATCH_ACTION_API = re.compile(
    r"^/api/groups/([A-Za-z0-9_-]+)/patches/([A-Za-z0-9_-]+)/(complete|rollback)$"
)
_GROUP_ROLLBACK_API = re.compile(r"^/api/groups/([A-Za-z0-9_-]+)/rollback$")
_MAX_BODY = 8192


def make_request_handler(manager: ReviewManager) -> type[BaseHTTPRequestHandler]:
    class ReviewRequestHandler(BaseHTTPRequestHandler):
        server_version = "code-harness-review"
        sys_version = ""

        def do_GET(self) -> None:
            if not self._valid_host():
                self._json_error(HTTPStatus.BAD_REQUEST, "Invalid host.")
                return
            parsed = urlsplit(self.path)
            path = parsed.path
            if path.startswith("/r/"):
                self._establish(path.removeprefix("/r/"))
                return
            session = self._browser_session()
            if session is None:
                self._json_error(HTTPStatus.UNAUTHORIZED, "Review session is required.")
                return
            if path == "/":
                self._serve_page(session)
                return
            if asset := _ASSETS.get(path):
                self._serve_asset(*asset)
                return
            if path == "/api/groups":
                limit_values = parse_qs(parsed.query).get("limit", ["50"])
                try:
                    limit = int(limit_values[0])
                    self._send_json(manager.service.list_groups(limit=limit).to_dict())
                except ValueError as error:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if path == "/api/groups/current":
                self._send_json(
                    manager.service.get_patch(
                        session.selected_group_id,
                        session.selected_transaction_id,
                    ).to_dict()
                )
                return
            if match := _FILE_API.fullmatch(path):
                if not self._owns_workspace(session):
                    return
                full_context = parse_qs(parsed.query).get("context") == ["full"]
                try:
                    review_file = manager.service.get_file(
                        match.group(1),
                        match.group(2),
                        int(match.group(3)),
                        collapse_context=not full_context,
                    )
                except HarnessError as error:
                    self._send_json(
                        {"error": error.message, "code": error.code},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                self._send_json(review_file.to_dict())
                return
            if match := _PATCH_API.fullmatch(path):
                if not self._owns_workspace(session):
                    return
                try:
                    patch = manager.service.get_patch(match.group(1), match.group(2))
                except HarnessError as error:
                    self._send_json(
                        {"error": error.message, "code": error.code},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                self._send_json(patch.to_dict())
                return
            if match := _GROUP_API.fullmatch(path):
                if not self._owns_workspace(session):
                    return
                try:
                    group = manager.service.get_group(match.group(1))
                except HarnessError as error:
                    self._send_json(
                        {"error": error.message, "code": error.code},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                self._send_json(group.to_dict())
                return
            self._json_error(HTTPStatus.NOT_FOUND, "Route not found.")

        def do_POST(self) -> None:
            if not self._valid_host() or self.headers.get("Origin") != manager.origin:
                self._json_error(HTTPStatus.FORBIDDEN, "Request origin was rejected.")
                return
            session = self._browser_session()
            if session is None:
                self._json_error(HTTPStatus.UNAUTHORIZED, "Review session is required.")
                return
            if self.headers.get("X-CSRF-Token") != session.csrf_token:
                self._json_error(HTTPStatus.FORBIDDEN, "CSRF token was rejected.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json_error(HTTPStatus.BAD_REQUEST, "Invalid request body.")
                return
            if length < 0 or length > _MAX_BODY:
                self._json_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body is too large.")
                return
            if length:
                self.rfile.read(length)
            path = urlsplit(self.path).path
            patch_action = _PATCH_ACTION_API.fullmatch(path)
            group_rollback = _GROUP_ROLLBACK_API.fullmatch(path)
            if patch_action is None and group_rollback is None:
                self._json_error(HTTPStatus.NOT_FOUND, "Route not found.")
                return
            if not self._owns_workspace(session):
                return
            try:
                if group_rollback is not None:
                    payload = manager.service.rollback_group(group_rollback.group(1)).to_dict()
                else:
                    assert patch_action is not None
                    group_id, transaction_id, action = patch_action.groups()
                    patch_summary = (
                        manager.service.complete(group_id, transaction_id)
                        if action == "complete"
                        else manager.service.rollback(group_id, transaction_id)
                    )
                    payload = patch_summary.to_dict()
                self._send_json(payload)
            except PatchRollbackConflictError as error:
                self._send_json(
                    {
                        "error": error.message,
                        "code": error.code,
                        "conflicts": list(error.paths),
                    },
                    status=HTTPStatus.CONFLICT,
                )
            except HarnessError as error:
                self._send_json(
                    {"error": error.message, "code": error.code},
                    status=HTTPStatus.CONFLICT,
                )

        def do_OPTIONS(self) -> None:
            self._json_error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed.")

        def log_message(self, _format: str, *_args: Any) -> None:
            # Tokens must never leak through the standard HTTP request log.
            return

        def _establish(self, token: str) -> None:
            established = manager.security.establish_session(token)
            if established is None:
                self._json_error(HTTPStatus.NOT_FOUND, "Review link is invalid or expired.")
                return
            session_id, _session = established
            self.send_response(HTTPStatus.SEE_OTHER)
            self._security_headers()
            self.send_header(
                "Set-Cookie",
                f"code_harness_review={session_id}; Path=/; HttpOnly; SameSite=Strict",
            )
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _serve_page(self, session: BrowserSession) -> None:
            template = (_STATIC_ROOT / "review.html").read_text(encoding="utf-8")
            page = template.replace(
                "__GROUP_ID__",
                html.escape(session.selected_group_id, quote=True),
            ).replace(
                "__TRANSACTION_ID__",
                html.escape(session.selected_transaction_id, quote=True),
            ).replace(
                "__CSRF_TOKEN__",
                html.escape(session.csrf_token, quote=True),
            )
            self._send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")

        def _serve_asset(self, filename: str, content_type: str) -> None:
            self._send_bytes((_STATIC_ROOT / filename).read_bytes(), content_type)

        def _browser_session(self) -> BrowserSession | None:
            raw_cookie = self.headers.get("Cookie", "")
            cookie = SimpleCookie()
            try:
                cookie.load(raw_cookie)
            except ValueError:
                return None
            morsel = cookie.get("code_harness_review")
            return manager.security.get_session(morsel.value if morsel else None)

        def _owns_workspace(self, session: BrowserSession) -> bool:
            if session.workspace_id == manager.service.history.workspace_id:
                return True
            self._json_error(HTTPStatus.FORBIDDEN, "Review access was rejected.")
            return False

        def _valid_host(self) -> bool:
            return self.headers.get("Host") == manager.origin.removeprefix("http://")

        def _send_json(
            self,
            value: dict[str, object],
            *,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self._send_bytes(payload, "application/json; charset=utf-8", status=status)

        def _json_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status=status)

        def _send_bytes(
            self,
            payload: bytes,
            content_type: str,
            *,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self._security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'",
            )

    return ReviewRequestHandler
