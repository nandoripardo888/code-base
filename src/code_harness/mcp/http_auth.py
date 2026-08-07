"""API-key gate for Streamable HTTP (no OAuth discovery)."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def extract_presented_key(request: Request) -> str | None:
    """Return the credential from Authorization: Bearer or X-Api-Key."""
    auth = request.headers.get("authorization")
    if auth is not None:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    api_key = request.headers.get("x-api-key")
    if api_key is not None and api_key.strip():
        return api_key.strip()
    return None


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject requests that do not present the configured API key."""

    def __init__(self, app: object, *, api_key: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._api_key = api_key

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        presented = extract_presented_key(request)
        if presented is None or not hmac.compare_digest(presented, self._api_key):
            # No WWW-Authenticate: Bearer - that header makes Claude.ai start an
            # OAuth client-registration flow this server does not implement.
            return JSONResponse(
                {"error": "unauthorized", "message": "Valid API key required."},
                status_code=401,
            )
        return await call_next(request)
