"""Single-user OAuth 2.0 authorization server for the remote Garmin MCP server.

This is deliberately small: there's exactly one real user (you), so there's
no user database, no persistent token store, and no third-party identity
provider. It exists only so claude.ai's "Register automatically" (Dynamic
Client Registration, RFC 7591) connector flow has something to talk to, and
so a bearer token — not the URL alone — is what lets a client call your
Garmin tools.

How it fits together:

- Any MCP client (e.g. claude.ai) can dynamically *register* itself
  (POST /register) — that's expected and normal; a registered client still
  can't get a token without your password.
- /authorize redirects the browser to our own /login page (see
  `build_login_routes`), which asks for `GARMIN_MCP_ACCESS_PASSWORD` — a
  password *you* choose for this server, separate from and unrelated to
  your Garmin account password, which this remote server never sees.
- Only after that password is entered correctly do we mint an
  authorization code, which the client then exchanges for a bearer access
  token (and refresh token) at /token.

Everything is held in memory (clients, pending logins, codes, tokens). On
a Railway restart/redeploy, every connected client has to redo the OAuth
dance once (claude.ai does this transparently) — there's no durable "token
database" to keep in sync, which is the right trade for a single-user
personal server.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass
from html import escape as _esc

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

AUTH_CODE_TTL_SECONDS = 5 * 60
ACCESS_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days; refreshed well before Garmin's own key expires
PENDING_LOGIN_TTL_SECONDS = 10 * 60


@dataclass
class _PendingLogin:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    created_at: float


class SingleUserOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """OAuthAuthorizationServerProvider gated by one shared password.

    Not a general-purpose multi-tenant implementation — every access
    token this issues is "you", because only you know the password.
    """

    def __init__(self, access_password: str, resource_url: str) -> None:
        self._access_password = access_password
        # This server only ever serves one RFC 8707 resource — itself — so every
        # token it mints is stamped with that resource regardless of what a client
        # requests, and AuthSettings.validate_token_resource can safely require it.
        self._resource_url = resource_url
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending_logins: dict[str, _PendingLogin] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}

    def check_password(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self._access_password.encode())

    # -- Dynamic client registration --------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info

    # -- Authorization code flow -------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._gc_pending_logins()
        state_key = secrets.token_urlsafe(24)
        self.pending_logins[state_key] = _PendingLogin(client=client, params=params, created_at=time.time())
        return f"/login?state_key={state_key}"

    def complete_login(self, state_key: str) -> tuple[_PendingLogin, str] | None:
        """Mint an authorization code for a pending login and build its redirect URL.

        Returns (pending, redirect_url), or None if state_key is unknown/expired
        (the caller should show the login form again with an error).
        """
        pending = self.pending_logins.pop(state_key, None)
        if pending is None or time.time() - pending.created_at > PENDING_LOGIN_TTL_SECONDS:
            return None

        code = secrets.token_urlsafe(32)
        self.auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=pending.params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            client_id=pending.client.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
        )
        redirect_url = construct_redirect_uri(
            str(pending.params.redirect_uri), code=code, state=pending.params.state
        )
        return pending, redirect_url

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self.auth_codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Single use: the token handler already validated expiry/PKCE/redirect_uri.
        self.auth_codes.pop(authorization_code.code, None)
        return self._issue_tokens(client.client_id, authorization_code.scopes)

    # -- Refresh tokens -----------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = self.refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate both tokens.
        self.refresh_tokens.pop(refresh_token.token, None)
        for access_token, info in list(self.access_tokens.items()):
            if info.client_id == client.client_id:
                self.access_tokens.pop(access_token, None)
        return self._issue_tokens(client.client_id, scopes or refresh_token.scopes)

    # -- Access tokens --------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self.access_tokens.get(token)
        if access_token is None:
            return None
        if access_token.expires_at is not None and access_token.expires_at < time.time():
            self.access_tokens.pop(token, None)
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self.access_tokens.pop(token.token, None)
        else:
            self.refresh_tokens.pop(token.token, None)

    # -- helpers --------------------------------------------------------------

    def _issue_tokens(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        expires_at = time.time() + ACCESS_TOKEN_TTL_SECONDS
        self.access_tokens[access_token] = AccessToken(
            token=access_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(expires_at),
            resource=self._resource_url,
        )
        self.refresh_tokens[refresh_token] = RefreshToken(token=refresh_token, client_id=client_id, scopes=scopes)
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token,
            scope=" ".join(scopes) if scopes else None,
        )

    def _gc_pending_logins(self) -> None:
        cutoff = time.time() - PENDING_LOGIN_TTL_SECONDS
        expired = [key for key, pending in self.pending_logins.items() if pending.created_at < cutoff]
        for key in expired:
            self.pending_logins.pop(key, None)


def _login_page(state_key: str, *, error: str | None = None) -> str:
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Garmin Connect MCP — sign in</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #f4f4f5; color: #18181b;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
  form {{ background: #fff; padding: 2rem; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1);
          width: 100%; max-width: 360px; }}
  h1 {{ font-size: 1.1rem; margin: 0 0 .25rem; }}
  p.sub {{ color: #71717a; font-size: .85rem; margin: 0 0 1.25rem; }}
  label {{ display: block; font-size: .85rem; margin-bottom: .4rem; }}
  input {{ width: 100%; padding: .6rem .7rem; border: 1px solid #d4d4d8; border-radius: 8px;
           font-size: 1rem; box-sizing: border-box; }}
  button {{ margin-top: 1rem; width: 100%; padding: .65rem; border: none; border-radius: 8px;
            background: #18181b; color: #fff; font-size: .95rem; cursor: pointer; }}
  .error {{ color: #b91c1c; font-size: .85rem; margin: 0 0 1rem; }}
</style>
</head>
<body>
  <form method="post" action="/login">
    <h1>Garmin Connect MCP</h1>
    <p class="sub">Enter this server's access password to let this client use your Garmin data tools.</p>
    {error_html}
    <input type="hidden" name="state_key" value="{_esc(state_key)}">
    <label for="password">Access password</label>
    <input type="password" name="password" id="password" autofocus required>
    <button type="submit">Continue</button>
  </form>
</body>
</html>"""


def build_login_routes(mcp: MCPServer, provider: SingleUserOAuthProvider) -> None:
    """Register the /login GET+POST routes the provider's /authorize redirect sends to.

    Kept out of create_auth_routes() (which only knows the standard OAuth
    endpoints) via MCPServer.custom_route — the supported way to add
    extra, unauthenticated HTTP routes for an auth flow.
    """

    @mcp.custom_route("/login", methods=["GET"])
    async def login_form(request: Request) -> Response:
        state_key = request.query_params.get("state_key", "")
        if state_key not in provider.pending_logins:
            return HTMLResponse("This sign-in link has expired. Go back to Claude and try connecting again.", status_code=400)
        return HTMLResponse(_login_page(state_key))

    @mcp.custom_route("/login", methods=["POST"])
    async def login_submit(request: Request) -> Response:
        form = await request.form()
        state_key = str(form.get("state_key", ""))
        password = str(form.get("password", ""))

        pending = provider.pending_logins.get(state_key)
        if pending is None:
            return HTMLResponse("This sign-in link has expired. Go back to Claude and try connecting again.", status_code=400)

        if not provider.check_password(password):
            return HTMLResponse(_login_page(state_key, error="Wrong password — try again."), status_code=401)

        result = provider.complete_login(state_key)
        if result is None:
            return HTMLResponse("This sign-in link has expired. Go back to Claude and try connecting again.", status_code=400)
        _pending, redirect_url = result
        return RedirectResponse(url=redirect_url, status_code=302)
