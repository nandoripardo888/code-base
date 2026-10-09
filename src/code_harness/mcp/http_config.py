"""Resolve MCP transport and Streamable HTTP settings from flags and env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

HttpTransport = Literal["streamable-http"]
McpTransport = Literal["stdio", "streamable-http"]

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_HTTP_PATH = "/mcp"

# Kept when customizing Host allowlists so local probes still work.
_LOOPBACK_ALLOWED_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
    "[::1]",
    "[::1]:*",
)
_LOOPBACK_ALLOWED_ORIGINS = (
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
)
_DEFAULT_REMOTE_ORIGINS = (
    "https://claude.ai",
    "https://claude.com",
)


def host_header_from_url(url: str) -> str | None:
    """Host header value implied by a public connector URL (no default ports)."""
    parsed = urlsplit(url.strip())
    hostname = parsed.hostname
    if not hostname:
        return None
    if parsed.port is not None and parsed.port not in (80, 443):
        return f"{hostname}:{parsed.port}"
    return hostname


def origin_from_url(url: str) -> str | None:
    """Origin (scheme://host[:port]) implied by a public connector URL."""
    parsed = urlsplit(url.strip())
    if not parsed.scheme or not parsed.hostname:
        return None
    if parsed.port is not None and parsed.port not in (80, 443):
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    return f"{parsed.scheme}://{parsed.hostname}"


def merge_unique(*groups: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for item in group:
            if item not in seen:
                seen.add(item)
                out.append(item)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class HttpServeConfig:
    """Listen settings for an HTTP MCP transport."""

    transport: HttpTransport
    host: str
    port: int
    path: str
    api_key: str | None
    public_url: str | None
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    disable_dns_rebinding: bool = False

    @property
    def is_loopback(self) -> bool:
        return self.host.lower() in _LOOPBACK_HOSTS

    @property
    def is_public(self) -> bool:
        """Whether this endpoint is intended to be reachable beyond loopback."""
        return self.public_url is not None or not self.is_loopback

    @property
    def connector_url(self) -> str:
        """URL to paste into a remote MCP client (Claude custom connector)."""
        if self.public_url:
            return self.public_url.rstrip("/")
        display_host = "127.0.0.1" if self.is_loopback else self.host
        return f"http://{display_host}:{self.port}{self.path}"


def _env(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _csv_env(name: str) -> tuple[str, ...]:
    raw = _env(name)
    if raw is None:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _env_flag(name: str) -> bool:
    raw = _env(name)
    if raw is None:
        return False
    return raw.lower() in {"1", "true", "yes", "on"}


def resolve_mcp_transport(transport: str | None = None) -> McpTransport:
    """Resolve MCP transport from an explicit value, env, or the stdio default."""
    resolved = transport or _env("CODE_HARNESS_MCP_TRANSPORT") or "stdio"
    if resolved not in {"stdio", "streamable-http"}:
        raise ValueError(
            "Unsupported MCP transport. Use 'stdio' or 'streamable-http'."
        )
    return cast(McpTransport, resolved)


def resolve_http_config(
    *,
    transport: HttpTransport,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
    api_key: str | None = None,
    public_url: str | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    disable_dns_rebinding: bool | None = None,
    no_api_key: bool = False,
    allow_public_without_api_key: bool = False,
) -> HttpServeConfig:
    """Merge CLI overrides with ``CODE_HARNESS_MCP_*`` environment variables."""
    resolved_host = host or _env("CODE_HARNESS_MCP_HOST") or DEFAULT_HTTP_HOST
    if port is not None:
        resolved_port = port
    else:
        raw_port = _env("CODE_HARNESS_MCP_PORT")
        resolved_port = int(raw_port) if raw_port is not None else DEFAULT_HTTP_PORT
    if not (0 < resolved_port < 65536):
        raise ValueError(f"MCP HTTP port out of range: {resolved_port}.")

    resolved_path = path or _env("CODE_HARNESS_MCP_PATH") or DEFAULT_HTTP_PATH
    if not resolved_path.startswith("/"):
        resolved_path = f"/{resolved_path}"

    # --no-api-key must win over User env CODE_HARNESS_MCP_API_KEY (Claude authless).
    if no_api_key or _env_flag("CODE_HARNESS_MCP_NO_API_KEY"):
        resolved_key = None
    elif api_key is not None:
        resolved_key = api_key
    else:
        resolved_key = _env("CODE_HARNESS_MCP_API_KEY")
    resolved_public = public_url if public_url is not None else _env("CODE_HARNESS_MCP_PUBLIC_URL")
    resolved_disable = (
        disable_dns_rebinding
        if disable_dns_rebinding is not None
        else _env_flag("CODE_HARNESS_MCP_DISABLE_DNS_REBINDING")
    )

    hosts = (
        tuple(allowed_hosts)
        if allowed_hosts is not None
        else _csv_env("CODE_HARNESS_MCP_ALLOWED_HOSTS")
    )
    origins = (
        tuple(allowed_origins)
        if allowed_origins is not None
        else _csv_env("CODE_HARNESS_MCP_ALLOWED_ORIGINS")
    )

    public_host = host_header_from_url(resolved_public) if resolved_public else None
    public_origin = origin_from_url(resolved_public) if resolved_public else None
    if not resolved_disable and (public_host or hosts or origins or public_origin):
        # Override MCPServer's localhost-only default so tunnels (Cloudflare, etc.) work.
        host_extras = (public_host, f"{public_host}:*") if public_host else ()
        origin_extras = ((public_origin,) if public_origin else ()) + _DEFAULT_REMOTE_ORIGINS
        hosts = merge_unique(_LOOPBACK_ALLOWED_HOSTS, hosts, host_extras)
        origins = merge_unique(_LOOPBACK_ALLOWED_ORIGINS, origins, origin_extras)

    config = HttpServeConfig(
        transport=transport,
        host=resolved_host,
        port=resolved_port,
        path=resolved_path,
        api_key=resolved_key,
        public_url=resolved_public,
        allowed_hosts=() if resolved_disable else hosts,
        allowed_origins=() if resolved_disable else origins,
        disable_dns_rebinding=resolved_disable,
    )
    validate_http_config(
        config,
        allow_public_without_api_key=allow_public_without_api_key,
    )
    return config


def validate_http_config(
    config: HttpServeConfig,
    *,
    allow_public_without_api_key: bool = False,
) -> None:
    """Refuse public HTTP without API-key protection unless explicitly allowed."""
    if config.is_public and not config.api_key and not allow_public_without_api_key:
        raise ValueError(
            "Non-loopback MCP HTTP requires an API key. Pass --api-key or set "
            "CODE_HARNESS_MCP_API_KEY."
        )
