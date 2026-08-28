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
    from code_harness.review.manager import ReviewHub
    from code_harness.review.security import BrowserSession

_STATIC_ROOT = Path(__file__).with_name("static")
_SESSION_COOKIE = "code_harness_review"
_MAX_BODY = 8192
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
_GROUP_ACTION_API = re.compile(r"^/api/groups/([A-Za-z0-9_-]+)/(complete|rollback)$")
_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def make_request_handler(manager: ReviewHub) -> type[BaseHTTPRequestHandler]:
    class ReviewRequestHandler(BaseHTTPRequestHandler):
        server_version = "code-harness-review"
        sys_version = ""
        _set_session_cookie: str | None = None

        def do_GET(self) -> None:
            self._set_session_cookie = None
            if not self._valid_host():
                self._json_error(HTTPStatus.BAD_REQUEST, "Invalid host.")
                return
            parsed = urlsplit(self.path)
            path = parsed.path
            if path in {"/review.css", "/static/review.css"}:
                self._serve_static_file("review.css")
                return
            if path in {"/review.js", "/static/review.js"}:
                self._serve_static_file("review.js")
                return
            if path.startswith(("/chunks/", "/assets/", "/fonts/")):
                self._serve_static_file(path.lstrip("/"))
                return
            if path == "/":
                session = self._require_session(create=True)
                if session is None:
                    return
                self._serve_page(session)
                return
            session = self._require_session(create=False)
            if session is None:
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
                try:
                    selection = manager.service.resolve_selection("latest")
                    self._send_json(
                        manager.service.get_patch(
                            selection.group_id,
                            selection.transaction_id,
                        ).to_dict()
                    )
                except HarnessError as error:
                    self._send_json(
                        {"error": error.message, "code": error.code},
                        status=HTTPStatus.NOT_FOUND,
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
            self._set_session_cookie = None
            if not self._valid_host() or self.headers.get("Origin") != manager.origin:
                self._json_error(HTTPStatus.FORBIDDEN, "Request origin was rejected.")
                return
            session = self._require_session(create=False)
            if session is None:
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
            group_action = _GROUP_ACTION_API.fullmatch(path)
            if patch_action is None and group_action is None:
                self._json_error(HTTPStatus.NOT_FOUND, "Route not found.")
                return
            if not self._owns_workspace(session):
                return
            try:
                if group_action is not None:
                    group_id, action = group_action.groups()
                    group_summary = (
                        manager.service.complete_group(group_id)
                        if action == "complete"
                        else manager.service.rollback_group(group_id)
                    )
                    payload = group_summary.to_dict()
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
            return

        def _serve_page(self, session: BrowserSession) -> None:
            template = (_STATIC_ROOT / "review.html").read_text(encoding="utf-8")
            page = template.replace(
                "__CSRF_TOKEN__",
                html.escape(session.csrf_token, quote=True),
            )
            self._send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")

        def _serve_static_file(self, relative: str) -> None:
            candidate = self._safe_static_path(relative)
            if candidate is None:
                self._json_error(HTTPStatus.NOT_FOUND, "Asset not found.")
                return
            content_type = _CONTENT_TYPES.get(
                candidate.suffix.lower(),
                "application/octet-stream",
            )
            self._send_bytes(candidate.read_bytes(), content_type)

        def _safe_static_path(self, relative: str) -> Path | None:
            cleaned = relative.replace("\\", "/").lstrip("/")
            if not cleaned or ".." in cleaned.split("/"):
                return None
            root = _STATIC_ROOT.resolve()
            candidate = (root / cleaned).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return None
            return candidate if candidate.is_file() else None

        def _require_session(self, *, create: bool) -> BrowserSession | None:
            session = self._browser_session()
            if session is not None:
                return session
            if not create:
                self._json_error(HTTPStatus.UNAUTHORIZED, "Review session is required.")
                return None
            session_id, session = manager.security.create_session(manager.security_scope)
            self._set_session_cookie = session_id
            return session

        def _browser_session(self) -> BrowserSession | None:
            raw_cookie = self.headers.get("Cookie", "")
            cookie = SimpleCookie()
            try:
                cookie.load(raw_cookie)
            except ValueError:
                return None
            morsel = cookie.get(_SESSION_COOKIE)
            return manager.security.get_session(morsel.value if morsel else None)

        def _owns_workspace(self, session: BrowserSession) -> bool:
            if session.workspace_id == manager.security_scope:
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
            if self._set_session_cookie is not None:
                self.send_header(
                    "Set-Cookie",
                    (
                        f"{_SESSION_COOKIE}={self._set_session_cookie}; "
                        "Path=/; HttpOnly; SameSite=Strict"
                    ),
                )
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
                "default-src 'none'; script-src 'self' 'wasm-unsafe-eval' blob:; "
                "worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; connect-src 'self'; img-src 'self' data:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )

    return ReviewRequestHandler
