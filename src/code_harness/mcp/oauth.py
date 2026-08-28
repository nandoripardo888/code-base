"""OAuth bearer-token verification for the MCP resource server."""

from __future__ import annotations

import asyncio
from typing import Any

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from code_harness.mcp.auth_config import OAuthConfig

_ALLOWED_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)


def _scopes_from_claims(claims: dict[str, Any]) -> list[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        return [part for part in scope.split() if part]
    if isinstance(scope, list) and all(isinstance(item, str) for item in scope):
        return list(scope)

    scp = claims.get("scp")
    if isinstance(scp, str):
        return [part for part in scp.split() if part]
    if isinstance(scp, list) and all(isinstance(item, str) for item in scp):
        return list(scp)
    return []


def _string_claim(claims: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = claims.get(name)
        if isinstance(value, str) and value:
            return value
    return None


class JwtTokenVerifier(TokenVerifier):
    """Validate asymmetric JWT access tokens against a configured JWKS endpoint."""

    def __init__(self, config: OAuthConfig, *, jwk_client: Any | None = None) -> None:
        self._config = config
        self._jwk_client = jwk_client or jwt.PyJWKClient(config.jwks_url)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if not isinstance(algorithm, str) or algorithm not in _ALLOWED_ALGORITHMS:
                return None

            signing_key = await asyncio.to_thread(
                self._jwk_client.get_signing_key_from_jwt,
                token,
            )
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm],
                audience=self._config.audience,
                issuer=self._config.issuer_url,
            )
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            return None

        client_id = _string_claim(claims, "client_id", "azp", "sub")
        if client_id is None:
            return None

        expires_at_raw = claims.get("exp")
        expires_at = int(expires_at_raw) if isinstance(expires_at_raw, int | float) else None
        subject = _string_claim(claims, "sub")
        resource = _string_claim(claims, "resource") or self._config.audience

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=_scopes_from_claims(claims),
            expires_at=expires_at,
            resource=resource,
            subject=subject,
            claims=claims,
        )
