"""In-memory unofficial-API Garmin session for the remote server, plus the
owner-only /admin/garmin-login page that establishes it.

Why this exists: the remote server is deliberately official-API-only by
default (see remote_server.py) specifically so your Garmin password never
has to live anywhere but your own machine. If you've decided the 11
unofficial-fallback tools (training status/readiness, VO2 max, race
predictions, personal records, devices, activity splits/laps/weather/
gear, respiration) are worth that trade-off, this module is the
lowest-risk way to do it:

- Your email and password are only ever typed into a page served by this
  same server, directly — they never pass through Claude or any chat
  transcript, and this module never writes them to disk, a log line, or
  an environment variable. The page itself is gated by the same
  `GARMIN_MCP_ACCESS_PASSWORD` that already protects the OAuth /login
  flow — the one thing standing between "anyone with the URL" and your
  data, same as everywhere else in this server.
- After a successful login, only the resulting *session* (the
  unofficial-API library's own small bundle of refresh tokens — not your
  password) is kept, and only in this process's memory.
- The trade-off for not persisting anything by default: a restart,
  redeploy, or (on Render's free tier) idle-spindown forgets the session,
  and you have to revisit /admin/garmin-login. If that's annoying, the
  success page shows an opaque session blob you can optionally save as
  the `GARMIN_UNOFFICIAL_SESSION` environment variable (see
  `resume_from_env`) — still not your password, and still nothing this
  module writes anywhere on its own.

Garmin may require a one-time MFA code the first time it sees a login
from a new location (which a cloud host's IP usually is). That's handled
as an explicit second step (`start_login` -> `complete_mfa`) rather than
a blocking prompt, since a web request can't pause for terminal input.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import threading
import time
from html import escape as _esc
from typing import Any

from garminconnect import Garmin
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from .remote_auth import SingleUserOAuthProvider

#: How long a pending MFA challenge stays valid before it's discarded.
MFA_PENDING_TTL_SECONDS = 5 * 60


class GarminAuthError(RuntimeError):
    """Raised when there is no usable unofficial-API Garmin session yet."""


class _PendingMFA:
    __slots__ = ("client_state", "created_at")

    def __init__(self, client_state: dict[str, Any]) -> None:
        self.client_state = client_state
        self.created_at = time.time()


_client: Garmin | None = None
_pending_mfa: dict[str, _PendingMFA] = {}
_lock = threading.Lock()


def get_client() -> Garmin:
    """Return the logged-in unofficial-API client, or raise GarminAuthError."""
    with _lock:
        if _client is not None:
            return _client
    raise GarminAuthError(
        "No unofficial-API Garmin session yet on this server. Its owner "
        "needs to log in once at the server's /admin/garmin-login page."
    )


def has_session() -> bool:
    with _lock:
        return _client is not None


def reset_session() -> None:
    """Drop the in-memory session and any pending MFA challenges.

    Not used by the server itself — for tests, so one test's login doesn't
    leak into the next (this module's state is a process-wide global).
    """
    global _client
    with _lock:
        _client = None
        _pending_mfa.clear()


def start_login(email: str, password: str) -> tuple[str, str | None]:
    """Begin a login. Returns (status, pending_id).

    status is "ok" (logged in immediately; pending_id is None) or
    "needs_mfa" (pending_id identifies the state `complete_mfa` needs).
    Raises GarminAuthError on failure (bad credentials, rate-limited, …).
    """
    _gc_pending_mfa()
    client = Garmin(email, password, return_on_mfa=True)
    try:
        mfa_status, client_state = client.login()
    except Exception as exc:  # noqa: BLE001 - surface clearly; never logs credentials
        raise GarminAuthError(f"Garmin login failed: {type(exc).__name__}: {exc}") from exc

    if mfa_status == "needs_mfa":
        pending_id = secrets.token_urlsafe(24)
        with _lock:
            _pending_mfa[pending_id] = _PendingMFA(client_state)
        return "needs_mfa", pending_id

    # return_on_mfa=True skips the library's own profile/settings load even
    # on a clean success (it's only meant to be skippable while MFA might
    # still be pending) — do the one step a normal login() would have done.
    try:
        client._load_profile_and_settings()
    except Exception as exc:  # noqa: BLE001
        raise GarminAuthError(f"Logged in, but couldn't load your profile: {type(exc).__name__}: {exc}") from exc

    _set_client(client)
    return "ok", None


def complete_mfa(pending_id: str, mfa_code: str) -> None:
    """Finish a login that was waiting on an MFA code. Raises GarminAuthError on failure.

    Deliberately doesn't consume `pending_id` on a wrong code: a mistyped
    code should let you retry with the same challenge (until it expires via
    the normal TTL sweep), not force you back to re-entering your password.
    """
    _gc_pending_mfa()
    with _lock:
        pending = _pending_mfa.get(pending_id)
    if pending is None:
        raise GarminAuthError("This MFA challenge has expired (or was already used). Start the login again.")

    client = Garmin(return_on_mfa=True)
    try:
        mfa_status, _legacy = client.resume_login(pending.client_state, mfa_code)
    except Exception as exc:  # noqa: BLE001
        raise GarminAuthError(f"MFA code rejected: {type(exc).__name__}: {exc}") from exc
    if mfa_status == "needs_mfa":
        raise GarminAuthError("Wrong MFA code — try again.")

    with _lock:
        _pending_mfa.pop(pending_id, None)
    _set_client(client)


def session_dump() -> str:
    """Return an opaque session-token JSON string (not a password) for optional persistence."""
    with _lock:
        if _client is None:
            raise GarminAuthError("No active session to save yet.")
        return _client.client.dumps()


def resume_from_env() -> bool:
    """On startup, try to resume a session saved earlier via GARMIN_UNOFFICIAL_SESSION.

    Returns True if a session was restored. Never raises — a missing,
    malformed, or stale value just means the owner logs in again at
    /admin/garmin-login, same as if this was never set.
    """
    token_json = os.environ.get("GARMIN_UNOFFICIAL_SESSION")
    if not token_json:
        return False
    client = Garmin()
    try:
        client.login(tokenstore=token_json)
    except Exception:  # noqa: BLE001 - stale/invalid session; fall back to the login page
        return False
    _set_client(client)
    return True


def _set_client(client: Garmin) -> None:
    global _client
    with _lock:
        _client = client


def _gc_pending_mfa() -> None:
    cutoff = time.time() - MFA_PENDING_TTL_SECONDS
    with _lock:
        expired = [k for k, v in _pending_mfa.items() if v.created_at < cutoff]
        for k in expired:
            _pending_mfa.pop(k, None)


# --------------------------------------------------------------------------
# /admin/garmin-login — owner-only page that drives the above
# --------------------------------------------------------------------------


_PAGE_STYLE = """
  body { font-family: -apple-system, system-ui, sans-serif; background: #f4f4f5; color: #18181b;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
  main { background: #fff; padding: 2rem; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1);
         width: 100%; max-width: 420px; }
  h1 { font-size: 1.1rem; margin: 0 0 .25rem; }
  p.sub { color: #71717a; font-size: .85rem; margin: 0 0 1.25rem; line-height: 1.4; }
  label { display: block; font-size: .85rem; margin: .9rem 0 .4rem; }
  input, textarea { width: 100%; padding: .6rem .7rem; border: 1px solid #d4d4d8; border-radius: 8px;
           font-size: .95rem; box-sizing: border-box; font-family: inherit; }
  textarea { font-family: ui-monospace, monospace; font-size: .8rem; height: 5rem; resize: vertical; }
  button { margin-top: 1.25rem; width: 100%; padding: .65rem; border: none; border-radius: 8px;
            background: #18181b; color: #fff; font-size: .95rem; cursor: pointer; }
  .error { color: #b91c1c; font-size: .85rem; margin: 0 0 1rem; }
  .ok { color: #15803d; font-size: .85rem; margin: 0 0 1rem; }
  hr { border: none; border-top: 1px solid #e4e4e7; margin: 1.25rem 0; }
"""


def _page(body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Garmin Connect MCP — connect Garmin account</title>
<style>{_PAGE_STYLE}</style>
</head>
<body><main>{body}</main></body>
</html>"""


def _credentials_form(*, error: str | None = None) -> str:
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    return _page(f"""
    <h1>Connect your Garmin account</h1>
    <p class="sub">This logs this server in to Garmin's unofficial API so it can
    serve the 11 unofficial-fallback tools. Your Garmin password is sent straight
    to this server and is never stored — only the resulting session is kept, in
    memory. See remote_auth_unofficial.py for the full trade-offs.</p>
    {error_html}
    <form method="post" action="/admin/garmin-login">
      <label for="access_password">This server's access password</label>
      <input type="password" name="access_password" id="access_password" autofocus required>
      <label for="garmin_email">Garmin Connect email</label>
      <input type="email" name="garmin_email" id="garmin_email" required>
      <label for="garmin_password">Garmin Connect password</label>
      <input type="password" name="garmin_password" id="garmin_password" required autocomplete="off">
      <button type="submit">Log in</button>
    </form>
    """)


def _mfa_form(pending_id: str, *, error: str | None = None) -> str:
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    return _page(f"""
    <h1>Enter your MFA code</h1>
    <p class="sub">Garmin wants to verify this login — check your email or the
    Garmin Connect app for a one-time code.</p>
    {error_html}
    <form method="post" action="/admin/garmin-login">
      <input type="hidden" name="pending_id" value="{_esc(pending_id)}">
      <label for="access_password">This server's access password</label>
      <input type="password" name="access_password" id="access_password" autofocus required>
      <label for="mfa_code">MFA code</label>
      <input type="text" name="mfa_code" id="mfa_code" inputmode="numeric" required>
      <button type="submit">Verify</button>
    </form>
    """)


def _success_page() -> str:
    try:
        dump = session_dump()
    except GarminAuthError:
        dump = ""
    optional = ""
    if dump:
        optional = f"""
        <hr>
        <p class="sub">Optional: to skip this page after the next restart, save
        this as the <code>GARMIN_UNOFFICIAL_SESSION</code> environment variable.
        It's an opaque session token, not your password — but treat it like one,
        since whoever has it can read your Garmin data.</p>
        <textarea readonly onclick="this.select()">{_esc(dump)}</textarea>
        """
    return _page(f"""
    <h1>Connected</h1>
    <p class="ok">The unofficial-fallback tools are now live.</p>
    {optional}
    """)


def build_garmin_login_routes(mcp: MCPServer, provider: SingleUserOAuthProvider) -> None:
    """Register the /admin/garmin-login GET+POST routes."""

    @mcp.custom_route("/admin/garmin-login", methods=["GET"])
    async def garmin_login_form(_request: Request) -> Response:
        return HTMLResponse(_credentials_form())

    @mcp.custom_route("/admin/garmin-login", methods=["POST"])
    async def garmin_login_submit(request: Request) -> Response:
        form = await request.form()
        access_password = str(form.get("access_password", ""))
        pending_id = form.get("pending_id")

        if not provider.check_password(access_password):
            if pending_id:
                return HTMLResponse(
                    _mfa_form(str(pending_id), error="Wrong access password — try again."), status_code=401
                )
            return HTMLResponse(_credentials_form(error="Wrong access password — try again."), status_code=401)

        if pending_id:
            mfa_code = str(form.get("mfa_code", "")).strip()
            if not mfa_code:
                return HTMLResponse(_mfa_form(str(pending_id), error="Enter the code."), status_code=400)
            try:
                await asyncio.to_thread(complete_mfa, str(pending_id), mfa_code)
            except GarminAuthError as exc:
                return HTMLResponse(_mfa_form(str(pending_id), error=str(exc)), status_code=401)
            return HTMLResponse(_success_page())

        email = str(form.get("garmin_email", "")).strip()
        password = str(form.get("garmin_password", ""))
        if not email or not password:
            return HTMLResponse(_credentials_form(error="Enter your Garmin email and password."), status_code=400)

        try:
            status, new_pending_id = await asyncio.to_thread(start_login, email, password)
        except GarminAuthError as exc:
            return HTMLResponse(_credentials_form(error=str(exc)), status_code=401)
        finally:
            # Belt-and-suspenders: drop our only references to the plaintext
            # password in this scope now that start_login is done with them.
            password = ""  # noqa: F841 - intentionally overwritten, not reused

        if status == "needs_mfa":
            assert new_pending_id is not None
            return HTMLResponse(_mfa_form(new_pending_id))
        return HTMLResponse(_success_page())
