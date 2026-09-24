"""Official Canva MCP endpoints.

These are the remote server URLs published by Canva. This package is a client
of that server. It does not implement a Canva MCP server.
"""

from __future__ import annotations

from urllib.parse import urlparse

from models.errors import AppError, ErrorCode

OFFICIAL_HOST = "mcp.canva.com"
OFFICIAL_MCP_URL = "https://mcp.canva.com/mcp"
OFFICIAL_AUTHORIZE_URL = "https://mcp.canva.com/authorize"
OFFICIAL_TOKEN_URL = "https://mcp.canva.com/token"
OFFICIAL_REGISTER_URL = "https://mcp.canva.com/register"


def require_canva_https_url(url: str, *, allow_unofficial: bool) -> str:
    """Reject non-HTTPS and non-Canva hosts unless an operator explicitly opts out."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise AppError(
            ErrorCode.CONFIGURATION_ERROR,
            "Canva endpoints must use HTTPS.",
            http_status=503,
        )
    if host != OFFICIAL_HOST and not allow_unofficial:
        raise AppError(
            ErrorCode.CONFIGURATION_ERROR,
            "Canva is configured with an unofficial endpoint. The official server is https://mcp.canva.com/mcp.",
            http_status=503,
        )
    return url.strip()


def allow_export_url(url: str) -> bool:
    """True for an HTTPS Canva export host that does not carry a token in the query."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    if host != "canva.com" and not host.endswith((".canva.com", ".canva.ai")):
        return False
    query = parsed.query.casefold()
    if any(marker in query for marker in ("access_token", "refresh_token", "code_verifier", "client_secret")):
        return False
    return True


def require_redirect_uri(uri: str) -> str:
    parsed = urlparse((uri or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return uri.strip()
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1"}:
        return uri.strip()
    raise AppError(
        ErrorCode.CONFIGURATION_ERROR,
        "The Canva redirect URI must be HTTPS, or HTTP on localhost.",
        http_status=503,
    )
