from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(RuntimeError):
    pass


def _https_url(name: str, value: str, *, allow_loopback_http: bool = False) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ConfigurationError(f"{name} has an invalid port")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} must be an origin or URL without credentials/query/fragment")
    if parsed.scheme != "https" and not (allow_loopback_http and loopback and parsed.scheme == "http"):
        raise ConfigurationError(f"{name} must use HTTPS")
    return value


@dataclass(frozen=True)
class Settings:
    backend_root: Path
    repo_root: Path
    host: str
    port: int
    public_origin: str
    oauth_issuer: str
    oauth_audience: str
    oauth_jwks_url: str
    transport: str = "streamable-http"
    required_scope: str = "mirrow.production.read"
    local_dev: bool = False
    query_timeout_ms: int = 300
    sqlite_busy_timeout_ms: int = 50
    max_rows: int = 50
    max_response_bytes: int = 262_144
    max_concurrent_reads: int = 2

    @property
    def state_dir(self) -> Path:
        return self.backend_root / "data" / "production-data-mcp"

    @property
    def resource_identifier(self) -> str:
        """Canonical URL serialized by MCP protected-resource metadata."""
        parsed = urlsplit(self.oauth_audience)
        if parsed.path in {"", "/"}:
            return self.oauth_audience.rstrip("/") + "/"
        return self.oauth_audience

    @property
    def issuer_identifier(self) -> str:
        parsed = urlsplit(self.oauth_issuer)
        if parsed.path in {"", "/"}:
            return self.oauth_issuer.rstrip("/") + "/"
        return self.oauth_issuer

    @classmethod
    def from_env(cls) -> "Settings":
        backend = Path(__file__).resolve().parents[1]
        repo = backend.parent
        transport = os.getenv("MIRROW_DATA_MCP_TRANSPORT", "streamable-http").strip().lower()
        if transport not in {"streamable-http", "stdio"}:
            raise ConfigurationError("MIRROW_DATA_MCP_TRANSPORT must be streamable-http or stdio")
        local_dev = os.getenv("MIRROW_DATA_MCP_LOCAL_DEV", "").strip() == "1"
        allow_http = local_dev

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ConfigurationError(f"missing required environment variable: {name}")
            return value

        if transport == "stdio":
            # The OpenAI secure tunnel is the authentication and network boundary.
            # No local listener exists and no OAuth material is accepted or needed.
            public_origin = "https://mirrow.invalid"
            issuer = ""
            audience = ""
            jwks = ""
        else:
            public_origin = _https_url(
                "MIRROW_DATA_MCP_PUBLIC_ORIGIN",
                required("MIRROW_DATA_MCP_PUBLIC_ORIGIN"),
                allow_loopback_http=allow_http,
            ).rstrip("/")
            issuer = _https_url(
                "MIRROW_DATA_MCP_OAUTH_ISSUER",
                required("MIRROW_DATA_MCP_OAUTH_ISSUER"),
                allow_loopback_http=allow_http,
            )
            audience = _https_url(
                "MIRROW_DATA_MCP_OAUTH_AUDIENCE",
                required("MIRROW_DATA_MCP_OAUTH_AUDIENCE"),
                allow_loopback_http=allow_http,
            ).rstrip("/")
            if urlsplit(public_origin).path not in {"", "/"}:
                raise ConfigurationError("MIRROW_DATA_MCP_PUBLIC_ORIGIN must not contain a path")
            if urlsplit(audience).path not in {"", "/"} or audience.rstrip("/") != public_origin.rstrip("/"):
                raise ConfigurationError("MIRROW_DATA_MCP_OAUTH_AUDIENCE must equal the public origin")
            jwks = _https_url(
                "MIRROW_DATA_MCP_OAUTH_JWKS_URL",
                required("MIRROW_DATA_MCP_OAUTH_JWKS_URL"),
                allow_loopback_http=allow_http,
            )
        host = os.getenv("MIRROW_DATA_MCP_HOST", "127.0.0.1").strip()
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ConfigurationError("the collector must bind to loopback; publish it through a hardened HTTPS proxy")
        port = int(os.getenv("MIRROW_DATA_MCP_PORT", "8768"))
        if port < 1024 or port > 65535:
            raise ConfigurationError("MIRROW_DATA_MCP_PORT is outside the allowed range")
        scope = os.getenv("MIRROW_DATA_MCP_SCOPE", "mirrow.production.read").strip()
        if not scope or any(ch.isspace() for ch in scope):
            raise ConfigurationError("MIRROW_DATA_MCP_SCOPE must be one OAuth scope token")
        return cls(
            backend_root=backend,
            repo_root=repo,
            host=host,
            port=port,
            public_origin=public_origin,
            oauth_issuer=issuer,
            oauth_audience=audience,
            oauth_jwks_url=jwks,
            transport=transport,
            required_scope=scope,
            local_dev=local_dev,
        )
