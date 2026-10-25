"""Remote (HTTP) MCP server — official Garmin Personal API tools by
default, with the unofficial-fallback and workout tools available as an
opt-in.

This is the server meant to be deployed somewhere public (Railway, Render,
etc.) so claude.ai can reach it from your phone, not just from a Desktop
app on one machine. To keep that safe, it is deliberately narrower than
the local hybrid server in server.py:

- The 23 tools backed by Garmin's official Personal API are always
  registered here (see tools_official.py) — no password, no MFA.
- Access is gated by OAuth 2.0 with Dynamic Client Registration (see
  remote_auth.py): claude.ai registers itself automatically, but minting
  an actual access token still requires your own `GARMIN_MCP_ACCESS_PASSWORD`
  — a password you choose for this server, unrelated to Garmin.
- The 11 unofficial-fallback read tools (see tools_unofficial.py) and the
  9 workout creation/scheduling/push tools (see tools_workouts.py — these
  write to your account, unlike everything else here) are also always
  *registered*, but only work once you've logged in to Garmin's
  unofficial API at this server's own `/admin/garmin-login` page (gated by
  the same `GARMIN_MCP_ACCESS_PASSWORD`). Until then they return a clear
  `{"error": ...}` telling you so. Your Garmin email/password are typed
  directly into that page — never into Claude, never into an env var —
  and only the resulting session, not the password, is kept, in memory by
  default. See remote_auth_unofficial.py for the full design and
  trade-offs before deciding whether to use it.

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
- `GARMIN_UNOFFICIAL_SESSION` — an opaque unofficial-API session token
  (not a password) produced by a prior `/admin/garmin-login` success page.
  Set it so the unofficial-fallback tools survive a restart/redeploy
  without revisiting that page. Treat it like a password: anyone who has
  it can read your Garmin data.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from . import remote_auth_unofficial
from .remote_auth import SingleUserOAuthProvider, build_login_routes
from .remote_auth_unofficial import build_garmin_login_routes
from .tools_official import OFFICIAL_TOOLS
from .tools_unofficial import build_unofficial_tools
from .tools_workouts import build_workout_tools


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
            "Read-only tools for the authenticated user's own Garmin Connect data. Most "
            "tools use Garmin's official Personal API; a smaller set — each docstring "
            "starts with '(unofficial API)' — falls back to an unofficial library for "
            "data Garmin hasn't exposed officially yet (training status/readiness, VO2 "
            "max, race predictions, personal records, devices, activity splits/laps/"
            "weather/gear, respiration), plus a set of workout tools (create/list/get/"
            "delete a structured workout, schedule it, push it to a device) that write "
            "to the account rather than just reading it. Both groups only work once "
            "this server's owner has logged in at its /admin/garmin-login page. Dates "
            "are ISO 'YYYY-MM-DD' strings; omit a date argument to default to today. "
            "If a tool returns an "
            "{'error': ...} object mentioning a missing/expired key or session, tell the "
            "user: the official Personal API key needs refreshing, or the unofficial "
            "session needs re-establishing at /admin/garmin-login — whichever the error "
            "names."
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
    for fn in build_unofficial_tools(remote_auth_unofficial.get_client):
        mcp.add_tool(fn)
    for fn in build_workout_tools(remote_auth_unofficial.get_client):
        mcp.add_tool(fn)

    # Pick up a session saved from a previous /admin/garmin-login visit, if
    # the owner opted into persisting one (see GARMIN_UNOFFICIAL_SESSION in
    # the module docstring above). A missing or stale value is silently a
    # no-op — the tools just report no session until the owner logs in.
    remote_auth_unofficial.resume_from_env()

    build_login_routes(mcp, provider)
    build_garmin_login_routes(mcp, provider)

    # The MCP endpoint itself is mounted at "/" (see streamable_http_path in
    # main()/build_server() callers below), matching the bare URL claude.ai's
    # custom-connector UI expects you to paste in — not "/mcp". A custom route
    # for GET "/" here would never be reached anyway, since Starlette matches
    # the MCP endpoint's route (registered first, any method) before this one.

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
        # claude.ai's custom-connector UI posts MCP messages to exactly the URL
        # you give it (the bare https://your-domain, no "/mcp" suffix), so the
        # endpoint has to live at "/" rather than the SDK's "/mcp" default.
        streamable_http_path="/",
    )


if __name__ == "__main__":
    main()
