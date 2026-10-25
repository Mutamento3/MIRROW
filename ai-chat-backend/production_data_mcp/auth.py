from __future__ import annotations

import asyncio
from typing import Any

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .config import Settings


class JwtTokenVerifier(TokenVerifier):
    """Verify externally issued JWTs. Configuration is fixed at process start."""

    _ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwks = PyJWKClient(
            settings.oauth_jwks_url,
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=300,
            timeout=2,
        )

    def _decode(self, token: str) -> dict[str, Any]:
        if len(token) > 16_384:
            raise ValueError("oversized token")
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in self._ALGORITHMS or not header.get("kid"):
            raise ValueError("unsupported token header")
        signing_key = self._jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=list(self._ALGORITHMS),
            audience=self.settings.resource_identifier,
            issuer=self.settings.issuer_identifier,
            leeway=30,
            options={"require": ["exp", "iat", "sub"]},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await asyncio.wait_for(asyncio.to_thread(self._decode, token), timeout=3.0)
            raw_scopes = claims.get("scope", claims.get("scp", []))
            scopes = raw_scopes.split() if isinstance(raw_scopes, str) else list(raw_scopes)
            if self.settings.required_scope not in scopes:
                return None
            subject = str(claims["sub"])
            client_id = str(claims.get("azp") or claims.get("client_id") or subject)
            return AccessToken(
                token=token,
                client_id=client_id,
                scopes=scopes,
                expires_at=int(claims["exp"]),
                resource=self.settings.resource_identifier,
                subject=subject,
                claims={"iss": self.settings.issuer_identifier, "aud": self.settings.resource_identifier},
            )
        except Exception:
            return None
