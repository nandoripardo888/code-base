"""Authentication mode and OAuth resource-server configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, cast

AuthMode = Literal["none", "api-key", "oauth"]


def _env(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _env_flag(name: str) -> bool:
    value = _env(name)
    return value is not None and value.lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> tuple[str, ...]:
    value = _env(name)
    if value is None:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def resolve_auth_mode(
    auth: str | None = None,
    *,
    api_key: str | None = None,
    no_api_key: bool = False,
) -> AuthMode:
    """Resolve auth mode while preserving the existing API-key/no-key behavior."""
    explicit = auth or _env("CODE_HARNESS_MCP_AUTH")
    if explicit is not None:
        if explicit not in {"none", "api-key", "oauth"}:
            raise ValueError("Unsupported MCP auth mode. Use 'none', 'api-key', or 'oauth'.")
        return cast(AuthMode, explicit)

    if no_api_key or _env_flag("CODE_HARNESS_MCP_NO_API_KEY"):
        return "none"
    if api_key is not None or _env("CODE_HARNESS_MCP_API_KEY") is not None:
        return "api-key"
    return "none"


@dataclass(frozen=True, slots=True)
class OAuthConfig:
    """Configuration required for code-harness to act as an OAuth resource server."""

    issuer_url: str
    jwks_url: str
    audience: str
    resource_server_url: str
    scopes: tuple[str, ...]


def resolve_oauth_config(
    *,
    issuer_url: str | None = None,
    jwks_url: str | None = None,
    audience: str | None = None,
    resource_server_url: str | None = None,
    public_url: str | None = None,
    scopes: list[str] | None = None,
) -> OAuthConfig:
    """Resolve OAuth settings from CLI values and CODE_HARNESS_MCP_OAUTH_* env vars."""
    resolved_issuer = issuer_url or _env("CODE_HARNESS_MCP_OAUTH_ISSUER_URL")
    resolved_jwks = jwks_url or _env("CODE_HARNESS_MCP_OAUTH_JWKS_URL")
    resolved_audience = audience or _env("CODE_HARNESS_MCP_OAUTH_AUDIENCE")
    resolved_resource = (
        resource_server_url
        or _env("CODE_HARNESS_MCP_OAUTH_RESOURCE_URL")
        or public_url
        or _env("CODE_HARNESS_MCP_PUBLIC_URL")
    )
    resolved_scopes = (
        tuple(part.strip() for value in scopes for part in value.split(",") if part.strip())
        if scopes is not None
        else _csv_env("CODE_HARNESS_MCP_OAUTH_SCOPES")
    )

    missing: list[str] = []
    if resolved_issuer is None:
        missing.append("issuer URL")
    if resolved_jwks is None:
        missing.append("JWKS URL")
    if resolved_audience is None:
        missing.append("audience")
    if resolved_resource is None:
        missing.append("resource server URL/public URL")
    if missing:
        raise ValueError(f"OAuth configuration missing: {', '.join(missing)}.")

    assert resolved_issuer is not None
    assert resolved_jwks is not None
    assert resolved_audience is not None
    assert resolved_resource is not None

    return OAuthConfig(
        issuer_url=resolved_issuer,
        jwks_url=resolved_jwks,
        audience=resolved_audience,
        resource_server_url=resolved_resource,
        scopes=resolved_scopes,
    )
