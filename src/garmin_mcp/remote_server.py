"""Remote (HTTP) MCP server — official Garmin Personal API tools only.

This is the server meant to be deployed somewhere public (Railway, etc.)
so claude.ai can reach it from your phone, not just from a Desktop app on
one machine. To keep that safe, it is deliberately narrower than the
local hybrid server in server.py:

- Only the 23 tools backed by Garmin's official Personal API are
  registered here (see tools_official.py) — never the 11
  unofficial-fallback tools, and never your Garmin email/password. Those
  stay Desktop-only, via the separate `garmin-mcp-server` entry point.
- Access is gated by OAuth 2.0 with Dynamic Client Registration (see
  remote_auth.py): claude.ai registers itself automatically, but minting
  an actual access token still requires your own `GARMIN_MCP_ACCESS_PASSWORD`
  — a password you choose for this server, unrelated to Garmin.

Required environment variables:

- `GARMIN_PERSONAL_API_KEY` — your Garmin Personal API key (same as the
  local server; see auth_official.py). On a host like Railway, set this
  as a secret environment variable rather than running
  `garmin-mcp-save-key` (there's no interactive terminal on a deploy).
- `GARMIN_MCP_PUBLIC_URL` — this server's own public HTTPS URL once
  deployed, e.g. `https://garmin-mcp.up.railway.app` (no trailing slash
  needed). Used as the OAuth issuer/resource URL and for Host-header
  validation.
- `GARMIN_MCP_ACCESS_PASSWORD` — a password of your choosing that gates
  who can obtain a token for this server. Pick something you don't use
  anywhere else; it's the one thing standing between "anyone with the
  URL" and your data.

Optional:

- `PORT` — defaults to 8000 (Railway sets this itself).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from .remote_auth import SingleUserOAuthProvider, build_login_routes
from .tools_official import OFFICIAL_TOOLS


class _ConfigError(RuntimeError):
    pass


def _require_env(name: str, hint: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise _ConfigError(f"Set the {name} environment variable: {hint}")
    return value


def build_server() -> tuple[MCPServer, TransportSecuritySettings]:
    public_url = _require_env(
        "GARMIN_MCP_PUBLIC_URL",
        "this server's own public HTTPS URL once deployed, e.g. https://garmin-mcp.up.railway.app",
    ).rstrip("/")
    access_password = _require_env(
        "GARMIN_MCP_ACCESS_PASSWORD",
        "a password of your own choosing that gates who can get a token for this server "
        "(separate from, and unrelated to, your Garmin account password)",
    )
    # Fail fast if the key is missing rather than letting every tool call error out one by one.
    _require_env(
        "GARMIN_PERSONAL_API_KEY",
        "your Garmin Personal API key (Garmin Connect website -> Settings -> Personal API Key)",
    )

    provider = SingleUserOAuthProvider(access_password=access_password, resource_url=public_url)

    host = urlparse(public_url).netloc  # includes port, if any

    mcp = MCPServer(
        name="garmin-connect-remote",
        instructions=(
            "Read-only tools for the authenticated user's own Garmin Connect data, via "
            "Garmin's official Personal API only — this remote server has no unofficial "
            "fallback and never touches a Garmin password. Dates are ISO 'YYYY-MM-DD' "
            "strings; omit a date argument to default to today. If a tool returns an "
            "{'error': ...} object mentioning a missing/expired key, the Garmin Personal "
            "API key configured on this server needs to be refreshed by its owner."
        ),
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url,
            validate_token_resource=True,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["garmin:read"],
                default_scopes=["garmin:read"],
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    for fn in OFFICIAL_TOOLS:
        mcp.add_tool(fn)

    build_login_routes(mcp, provider)

    @mcp.custom_route("/", methods=["GET"])
    async def root(_request: Request) -> Response:
        return PlainTextResponse("garmin-connect-mcp (remote, official-API-only). See /.well-known/oauth-authorization-server.")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        return PlainTextResponse("ok")

    # DNS-rebinding protection is off by default when no TransportSecuritySettings is
    # passed to run(); supply one explicitly instead of relying on that default.
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, "localhost:*", "127.0.0.1:*"],
        allowed_origins=["https://claude.ai"],
    )

    return mcp, transport_security


def main() -> None:
    try:
        mcp, transport_security = build_server()
    except _ConfigError as exc:
        raise SystemExit(str(exc)) from exc

    port = int(os.environ.get("PORT", "8000"))
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=port,
        transport_security=transport_security,
    )


if __name__ == "__main__":
    main()
