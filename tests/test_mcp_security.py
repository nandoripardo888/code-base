from __future__ import annotations

import asyncio
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

import code_harness.mcp.server as server_module
from code_harness.mcp.auth_config import OAuthConfig, resolve_auth_mode, resolve_oauth_config
from code_harness.mcp.oauth import JwtTokenVerifier
from code_harness.mcp.server import create_server
from code_harness.mcp.tool_policy import PUBLIC_SAFE_TOOLS, resolve_tool_policy
from code_harness.session import Session


class _SigningKey:
    def __init__(self, key: object) -> None:
        self.key = key


class _JwkClient:
    def __init__(self, key: object) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, _token: str) -> _SigningKey:
        return _SigningKey(self._key)


def _oauth_config() -> OAuthConfig:
    return OAuthConfig(
        issuer_url="https://auth.example.com",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        audience="code-harness",
        resource_server_url="https://mcp.example.com/mcp",
        scopes=(),
    )


def _jwt_token(*, private_key: object, audience: str = "code-harness") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "https://auth.example.com",
            "aud": audience,
            "exp": now + 300,
            "iat": now,
            "sub": "user-1",
            "client_id": "claude-client",
            "scope": "code.read code.exec",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def test_tool_allowlist_limits_tools_list(session: Session) -> None:
    policy = resolve_tool_policy(["ServerInfo,ListProjects,ProjectInfo,Read"])
    server = create_server(session=session, tool_policy=policy)

    names = {tool.name for tool in asyncio.run(server.list_tools())}

    assert names == {"ServerInfo", "ListProjects", "ProjectInfo", "Read"}
    assert policy.required_scope("ListProjects") == "code.read"
    assert policy.required_scope("ProjectInfo") == "code.read"
    assert resolve_tool_policy(["ListPatchReviews"]).required_scope(
        "ListPatchReviews"
    ) == "code.read"
    assert resolve_tool_policy(["ReloadProjects"]).required_scope("ReloadProjects") == "code.write"
    assert resolve_tool_policy(["CancelJob"]).required_scope("CancelJob") == "code.exec"


def test_tool_allowlist_rejects_unknown_tool() -> None:
    with pytest.raises(ValueError, match="Unknown MCP tool"):
        resolve_tool_policy(["DoesNotExist"])


def test_only_server_info_is_public_safe() -> None:
    assert {"ServerInfo"} == PUBLIC_SAFE_TOOLS
    assert resolve_tool_policy(["ServerInfo"]).is_public_safe is True
    assert resolve_tool_policy(["Read"]).is_public_safe is False
    assert resolve_tool_policy(["ListProjects"]).is_public_safe is False
    assert resolve_tool_policy(["ProjectInfo"]).is_public_safe is False
    assert resolve_tool_policy(["ReloadProjects"]).is_public_safe is False


def test_auth_mode_preserves_api_key_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_HARNESS_MCP_AUTH", raising=False)
    monkeypatch.delenv("CODE_HARNESS_MCP_NO_API_KEY", raising=False)
    monkeypatch.delenv("CODE_HARNESS_MCP_API_KEY", raising=False)

    assert resolve_auth_mode() == "none"
    assert resolve_auth_mode(api_key="secret") == "api-key"
    assert resolve_auth_mode("oauth") == "oauth"
    assert resolve_auth_mode("none", api_key="ignored") == "none"


def test_oauth_config_requires_resource_server_fields() -> None:
    with pytest.raises(ValueError, match="OAuth configuration missing"):
        resolve_oauth_config()

    config = resolve_oauth_config(
        issuer_url="https://auth.example.com",
        jwks_url="https://auth.example.com/jwks",
        audience="code-harness",
        public_url="https://mcp.example.com/mcp",
        scopes=["code.read,code.exec"],
    )
    assert config.resource_server_url == "https://mcp.example.com/mcp"
    assert config.scopes == ("code.read", "code.exec")


def test_jwt_verifier_accepts_valid_token_and_rejects_wrong_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = JwtTokenVerifier(
        _oauth_config(),
        jwk_client=_JwkClient(private_key.public_key()),
    )

    valid = asyncio.run(verifier.verify_token(_jwt_token(private_key=private_key)))
    invalid = asyncio.run(
        verifier.verify_token(_jwt_token(private_key=private_key, audience="other-service"))
    )

    assert valid is not None
    assert valid.client_id == "claude-client"
    assert valid.subject == "user-1"
    assert valid.scopes == ["code.read", "code.exec"]
    assert invalid is None


def test_oauth_server_publishes_resource_metadata_and_requires_bearer(session: Session) -> None:
    server = create_server(session=session, oauth_config=_oauth_config())
    initialize: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    with TestClient(server.streamable_http_app(), base_url="http://localhost:8000") as client:
        metadata = client.get("/.well-known/oauth-protected-resource/mcp")
        unauthorized = client.post("/mcp", json=initialize, headers=headers)

    assert metadata.status_code == 200
    assert metadata.json()["resource"] == "https://mcp.example.com/mcp"
    assert metadata.json()["authorization_servers"] == ["https://auth.example.com/"]
    assert unauthorized.status_code == 401
    challenge = unauthorized.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert "resource_metadata=" in challenge


def test_oauth_scope_policy_allows_read_and_blocks_missing_scope(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = resolve_tool_policy(["Read"], enforce_scopes=True)
    server = create_server(session=session, tool_policy=policy)

    monkeypatch.setattr(
        server_module,
        "get_access_token",
        lambda: AccessToken(token="ok", client_id="client", scopes=["code.read"]),
    )
    result = asyncio.run(server.call_tool("Read", {"path": "README.md", "limit": 1}))
    assert result

    monkeypatch.setattr(
        server_module,
        "get_access_token",
        lambda: AccessToken(token="no-scope", client_id="client", scopes=[]),
    )
    with pytest.raises(ToolError, match=r"OAuth scope 'code\.read' is required for Read"):
        asyncio.run(server.call_tool("Read", {"path": "README.md", "limit": 1}))
