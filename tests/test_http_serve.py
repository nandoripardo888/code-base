"""Tests for Streamable HTTP config and API-key middleware."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from typer.testing import CliRunner

from code_harness.cli import app
from code_harness.mcp.http_auth import ApiKeyMiddleware
from code_harness.mcp.http_config import (
    resolve_http_config,
    resolve_mcp_transport,
    validate_http_config,
)
from code_harness.mcp.server import _streamable_http_app, _transport_security, create_server
from code_harness.projects import ProjectRegistry
from code_harness.session import Session

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_mcp_http_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("CODE_HARNESS_MCP_"):
            monkeypatch.delenv(name, raising=False)


def _app_with_key(api_key: str) -> Starlette:
    async def ok(_request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    application = Starlette(routes=[Route("/mcp", endpoint=ok, methods=["GET", "POST"])])
    application.add_middleware(ApiKeyMiddleware, api_key=api_key)
    return application


def test_api_key_middleware_accepts_bearer_and_x_api_key() -> None:
    client = TestClient(_app_with_key("secret-token"))
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer secret-token"}).status_code == 200
    assert client.get("/mcp", headers={"X-Api-Key": "secret-token"}).status_code == 200


def test_resolve_mcp_transport_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_HARNESS_MCP_TRANSPORT", raising=False)
    assert resolve_mcp_transport() == "stdio"


def test_resolve_mcp_transport_uses_env_and_explicit_value_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_MCP_TRANSPORT", "streamable-http")
    assert resolve_mcp_transport() == "streamable-http"
    assert resolve_mcp_transport("stdio") == "stdio"


def test_resolve_mcp_transport_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="Unsupported MCP transport"):
        resolve_mcp_transport("sse")


def test_resolve_http_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_HARNESS_MCP_HOST", raising=False)
    monkeypatch.delenv("CODE_HARNESS_MCP_PORT", raising=False)
    monkeypatch.delenv("CODE_HARNESS_MCP_PATH", raising=False)
    monkeypatch.delenv("CODE_HARNESS_MCP_API_KEY", raising=False)
    monkeypatch.delenv("CODE_HARNESS_MCP_PUBLIC_URL", raising=False)
    config = resolve_http_config(transport="streamable-http")
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.path == "/mcp"
    assert config.api_key is None
    assert config.connector_url == "http://127.0.0.1:8000/mcp"


def test_resolve_http_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_HARNESS_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("CODE_HARNESS_MCP_PORT", "9001")
    monkeypatch.setenv("CODE_HARNESS_MCP_PATH", "mcp")
    monkeypatch.setenv("CODE_HARNESS_MCP_API_KEY", "env-key")
    monkeypatch.setenv("CODE_HARNESS_MCP_PUBLIC_URL", "https://mcp.example.com/mcp")
    config = resolve_http_config(transport="streamable-http")
    assert config.host == "0.0.0.0"
    assert config.port == 9001
    assert config.path == "/mcp"
    assert config.api_key == "env-key"
    assert config.connector_url == "https://mcp.example.com/mcp"
    assert "mcp.example.com" in config.allowed_hosts
    assert "https://mcp.example.com" in config.allowed_origins
    assert "https://claude.ai" in config.allowed_origins


def test_public_url_allows_cloudflare_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_HARNESS_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("CODE_HARNESS_MCP_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("CODE_HARNESS_MCP_DISABLE_DNS_REBINDING", raising=False)
    public = "https://brisbane-messages-cartridges-promising.trycloudflare.com/mcp"
    config = resolve_http_config(
        transport="streamable-http",
        public_url=public,
        api_key="secret",
        disable_dns_rebinding=False,
    )
    assert "brisbane-messages-cartridges-promising.trycloudflare.com" in config.allowed_hosts
    assert config.connector_url == public


def test_no_api_key_ignores_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_HARNESS_MCP_API_KEY", "env-secret")
    monkeypatch.delenv("CODE_HARNESS_MCP_NO_API_KEY", raising=False)
    config = resolve_http_config(transport="streamable-http", no_api_key=True)
    assert config.api_key is None

    monkeypatch.setenv("CODE_HARNESS_MCP_NO_API_KEY", "1")
    config_env = resolve_http_config(transport="streamable-http")
    assert config_env.api_key is None



def test_non_loopback_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        resolve_http_config(transport="streamable-http", host="0.0.0.0")


def test_validate_http_config_allows_loopback_without_key() -> None:
    config = resolve_http_config(transport="streamable-http", host="127.0.0.1", port=8000)
    validate_http_config(config)


def test_public_url_counts_as_public_even_on_loopback() -> None:
    with pytest.raises(ValueError, match="API key"):
        resolve_http_config(
            transport="streamable-http",
            host="127.0.0.1",
            public_url="https://mcp.example.com/mcp",
        )

    config = resolve_http_config(
        transport="streamable-http",
        host="127.0.0.1",
        public_url="https://mcp.example.com/mcp",
        allow_public_without_api_key=True,
    )
    assert config.is_public is True


def test_create_server_applies_http_settings(session: Session) -> None:
    config = resolve_http_config(
        transport="streamable-http",
        host="127.0.0.1",
        port=9010,
        path="custom-mcp",
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://mcp.example.com"],
    )
    server = create_server(session=session)

    app = _streamable_http_app(server, config)
    assert any(route.path == "/custom-mcp" for route in app.routes)
    security = _transport_security(config)
    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert "mcp.example.com" in security.allowed_hosts
    assert "https://mcp.example.com" in security.allowed_origins


def test_streamable_http_initialize_handshake(session: Session) -> None:
    config = resolve_http_config(transport="streamable-http")
    server = create_server(session=session)
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }

    with TestClient(
        _streamable_http_app(server, config), base_url="http://localhost:8000"
    ) as client:
        response = client.post("/mcp", json=initialize, headers=headers)

    assert response.status_code == 200
    assert response.headers.get("mcp-session-id")
    assert '"serverInfo"' in response.text
    assert '"code-harness"' in response.text


def test_one_streamable_http_session_routes_multiple_projects(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    (crm / "same.txt").write_text("crm", encoding="utf-8")
    (banco / "same.txt").write_text("banco", encoding="utf-8")
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    try:
        config = resolve_http_config(transport="streamable-http", no_api_key=True)
        server = create_server(registry=registry)
        base_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "phase9-client", "version": "1.0"},
            },
        }

        with TestClient(
            _streamable_http_app(server, config), base_url="http://localhost:8000"
        ) as client:
            initialized = client.post("/mcp", json=initialize, headers=base_headers)
            assert initialized.status_code == 200
            session_id = initialized.headers.get("mcp-session-id")
            assert session_id
            session_headers = {
                **base_headers,
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2025-06-18",
            }
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
            notified = client.post("/mcp", json=notification, headers=session_headers)
            assert notified.status_code == 202

            def call_tool(request_id: int, name: str, arguments: dict[str, object]) -> str:
                response = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                    headers=session_headers,
                )
                assert response.status_code == 200
                return response.text

            projects = call_tool(2, "ListProjects", {})
            crm_read = call_tool(3, "Read", {"path": "same.txt", "project": "crm"})
            banco_read = call_tool(4, "Read", {"path": "same.txt", "project": "banco"})

        assert '\\\"default\\\": \\\"crm\\\"' in projects
        assert '\\\"name\\\": \\\"banco\\\"' in projects
        assert "1|crm" in crm_read
        assert "1|banco" in banco_read
    finally:
        registry.shutdown()


def test_cli_serve_help_lists_http_options() -> None:
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.stdout
    assert "streamable-http" in result.stdout
    assert "--api-key" in result.stdout
    assert "--auth" in result.stdout
    assert "--tool-allowlist" in result.stdout
    assert "--oauth-issuer-url" in result.stdout
    assert "--project-config" in result.stdout


def test_cli_mcp_serve_help_lists_same_http_options() -> None:
    result = runner.invoke(app, ["mcp", "serve", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.stdout
    assert "streamable-http" in result.stdout
    assert "--api-key" in result.stdout
    assert "--auth" in result.stdout
    assert "--tool-allowlist" in result.stdout
    assert "--oauth-issuer-url" in result.stdout
    assert "--project-config" in result.stdout


def test_cli_rejects_public_no_auth_without_safe_allowlist(project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "serve",
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
            "--project",
            str(project),
        ],
    )
    assert result.exit_code == 1
    assert "Public MCP without authentication" in result.stderr
    assert "ServerInfo" in result.stderr
