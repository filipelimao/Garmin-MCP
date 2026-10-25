"""Offline tests for the remote (HTTP) server's OAuth flow and tool scope.

Exercises the real Starlette app end-to-end (dynamic client registration,
the password-gated /login page, PKCE code exchange, refresh rotation, and
bearer-token enforcement) rather than only unit-testing the provider in
isolation, since that's where a subtly wrong wiring (e.g. a missing RFC
8707 `resource` stamp) would actually show up. Never touches the real
Garmin Connect API.

Run with: pytest
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from garmin_mcp import remote_auth_unofficial
from garmin_mcp.remote_server import build_server

PUBLIC_URL = "https://garmin-mcp.example.test"
PASSWORD = "correct-horse-battery-staple"
REDIRECT_URI = "https://claude.ai/api/mcp/callback"


@pytest.fixture
def remote_env(monkeypatch):
    monkeypatch.setenv("GARMIN_MCP_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("GARMIN_MCP_ACCESS_PASSWORD", PASSWORD)
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "dummy-key-for-tests")
    monkeypatch.delenv("GARMIN_UNOFFICIAL_SESSION", raising=False)


@pytest.fixture(autouse=True)
def _reset_unofficial_session():
    # remote_auth_unofficial's session/pending-MFA state is a process-wide
    # global, so without this one test's login would leak into the next.
    remote_auth_unofficial.reset_session()
    yield
    remote_auth_unofficial.reset_session()


class _FakeGarminNoMFA:
    """Stand-in for garminconnect.Garmin: logs in immediately, no MFA."""

    def __init__(self, email: str | None = None, password: str | None = None, **_kwargs: object) -> None:
        self.email = email
        self.password = password
        self.client = SimpleNamespace(dumps=lambda: '{"di_token": "fake"}')

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        return None, None

    def _load_profile_and_settings(self) -> None:
        pass

    def get_devices(self) -> list[dict[str, int]]:
        return [{"deviceId": 1234}]

    def get_workouts(self, start: int = 0, limit: int = 100, **_kwargs: object) -> list[dict[str, str]]:
        return [{"workoutId": "999", "workoutName": "fake workout"}]

    def upload_workout(self, workout_json: object) -> dict[str, str]:
        return {"workoutId": "999"}


class _FakeGarminMFA:
    """Stand-in for garminconnect.Garmin: always demands MFA code "123456"."""

    def __init__(self, email: str | None = None, password: str | None = None, **_kwargs: object) -> None:
        self.email = email
        self.password = password
        self.client = SimpleNamespace(dumps=lambda: '{"di_token": "fake"}')

    def login(self, tokenstore: str | None = None) -> tuple[str, dict[str, str]]:
        return "needs_mfa", {"ticket": "abc"}

    def resume_login(self, client_state: dict[str, str], mfa_code: str) -> tuple[str | None, None]:
        assert client_state == {"ticket": "abc"}
        if mfa_code == "123456":
            return None, None
        return "needs_mfa", None

    def _load_profile_and_settings(self) -> None:
        pass


@pytest.fixture
def app(remote_env):
    mcp, transport_security = build_server()
    # Matches main()'s streamable_http_path="/" — claude.ai's custom-connector
    # UI posts to exactly the URL you give it, not a "/mcp" sub-path.
    return mcp, mcp.streamable_http_app(transport_security=transport_security, streamable_http_path="/")


@pytest.fixture
def client(app):
    _mcp, starlette_app = app
    with TestClient(starlette_app, base_url=PUBLIC_URL) as c:
        yield c


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _register_client(client: TestClient) -> tuple[str, str]:
    resp = client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    assert resp.status_code == 201, resp.text
    info = resp.json()
    return info["client_id"], info["client_secret"]


def _authorize_to_login(client: TestClient, client_id: str, challenge: str, state: str = "xyz") -> str:
    resp = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/login?state_key=")
    return parse_qs(urlparse(location).query)["state_key"][0]


def _login_and_get_code(client: TestClient, state_key: str, password: str) -> dict[str, str]:
    resp = client.post("/login", data={"state_key": state_key, "password": password}, follow_redirects=False)
    assert resp.status_code == 302, resp.text
    redirect = resp.headers["location"]
    assert redirect.startswith(REDIRECT_URI)
    qs = parse_qs(urlparse(redirect).query)
    return {"code": qs["code"][0], "state": qs.get("state", [None])[0]}


def _exchange_code(
    client: TestClient, *, code: str, client_id: str, client_secret: str, verifier: str
) -> dict[str, str]:
    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _full_login(client: TestClient, *, password: str = PASSWORD) -> dict[str, str]:
    client_id, client_secret = _register_client(client)
    verifier, challenge = _pkce_pair()
    state_key = _authorize_to_login(client, client_id, challenge)
    result = _login_and_get_code(client, state_key, password)
    tokens = _exchange_code(client, code=result["code"], client_id=client_id, client_secret=client_secret, verifier=verifier)
    return {**tokens, "client_id": client_id, "client_secret": client_secret}


# --------------------------------------------------------------------------
# Tool scope
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_server_registers_official_and_unofficial_tools(app):
    mcp, _starlette_app = app
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    # 23 official + 11 unofficial-fallback (read-only) + 9 workout tools
    # (create/list/get/delete/schedule/push) — all of the unofficial ones
    # are always *registered* on the remote server now, they just error
    # out until the owner logs in at /admin/garmin-login (see the tests
    # below).
    assert len(names) == 43
    assert "get_training_status" in names
    assert "get_max_metrics" in names
    assert "list_devices" in names
    assert "create_simple_interval_workout" in names
    assert "create_workout" in names
    assert "list_workouts" in names
    assert "get_workout" in names
    assert "delete_workout" in names
    assert "schedule_workout" in names
    assert "push_workout_to_device" in names
    assert "send_scheduled_workouts_to_devices" in names
    assert "get_workout_types" in names


# --------------------------------------------------------------------------
# Unofficial-fallback session (remote_auth_unofficial)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unofficial_tool_reports_no_session_before_login(app):
    mcp, _starlette_app = app
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "list_devices" in tools
    from garmin_mcp.tools_unofficial import build_unofficial_tools

    (list_devices_fn,) = [
        fn for fn in build_unofficial_tools(remote_auth_unofficial.get_client) if fn.__name__ == "list_devices"
    ]
    result = await list_devices_fn()
    assert "error" in result
    assert "/admin/garmin-login" in result["error"]


@pytest.mark.asyncio
async def test_workout_tool_reports_no_session_before_login(app):
    mcp, _starlette_app = app
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "list_workouts" in tools
    from garmin_mcp.tools_workouts import build_workout_tools

    (list_workouts_fn,) = [
        fn for fn in build_workout_tools(remote_auth_unofficial.get_client) if fn.__name__ == "list_workouts"
    ]
    result = await list_workouts_fn()
    assert "error" in result
    assert "/admin/garmin-login" in result["error"]


@pytest.mark.asyncio
async def test_workout_tool_works_after_login(monkeypatch):
    monkeypatch.setattr(remote_auth_unofficial, "Garmin", _FakeGarminNoMFA)
    status, _pending_id = remote_auth_unofficial.start_login("me@example.test", "hunter2")
    assert status == "ok"

    from garmin_mcp.tools_workouts import build_workout_tools

    tools = {fn.__name__: fn for fn in build_workout_tools(remote_auth_unofficial.get_client)}
    result = await tools["list_workouts"]()
    assert result == [{"workoutId": "999", "workoutName": "fake workout"}]

    result = await tools["create_simple_interval_workout"](
        sport="running",
        name="6x800",
        repeats=6,
        work_minutes=3,
        recovery_minutes=2,
        warmup_minutes=10,
        cooldown_minutes=5,
        work_target="heart_rate_zone",
        work_zone_number=4,
    )
    assert result == {"workoutId": "999"}

    # Bad input surfaces as the same {"error": ...} shape, never an exception.
    result = await tools["create_simple_interval_workout"](sport="walking", name="x", repeats=1)
    assert "error" in result
    assert "ValueError" in result["error"]


def test_start_login_no_mfa_establishes_session(monkeypatch):
    monkeypatch.setattr(remote_auth_unofficial, "Garmin", _FakeGarminNoMFA)
    status, pending_id = remote_auth_unofficial.start_login("me@example.test", "hunter2")
    assert status == "ok"
    assert pending_id is None
    assert remote_auth_unofficial.has_session()
    assert remote_auth_unofficial.get_client().get_devices() == [{"deviceId": 1234}]
    assert remote_auth_unofficial.session_dump() == '{"di_token": "fake"}'


def test_start_login_needs_mfa_then_complete_with_retry(monkeypatch):
    monkeypatch.setattr(remote_auth_unofficial, "Garmin", _FakeGarminMFA)
    status, pending_id = remote_auth_unofficial.start_login("me@example.test", "hunter2")
    assert status == "needs_mfa"
    assert pending_id
    assert not remote_auth_unofficial.has_session()

    # A wrong code doesn't burn the pending challenge — you can retry it.
    with pytest.raises(remote_auth_unofficial.GarminAuthError):
        remote_auth_unofficial.complete_mfa(pending_id, "000000")
    assert not remote_auth_unofficial.has_session()

    remote_auth_unofficial.complete_mfa(pending_id, "123456")
    assert remote_auth_unofficial.has_session()

    # The challenge is single-use once it succeeds.
    with pytest.raises(remote_auth_unofficial.GarminAuthError):
        remote_auth_unofficial.complete_mfa(pending_id, "123456")


def test_complete_mfa_unknown_pending_id_raises():
    with pytest.raises(remote_auth_unofficial.GarminAuthError):
        remote_auth_unofficial.complete_mfa("nonexistent", "123456")


def test_session_dump_without_session_raises():
    with pytest.raises(remote_auth_unofficial.GarminAuthError):
        remote_auth_unofficial.session_dump()


def test_resume_from_env_restores_session(monkeypatch):
    monkeypatch.setattr(remote_auth_unofficial, "Garmin", _FakeGarminNoMFA)
    monkeypatch.setenv("GARMIN_UNOFFICIAL_SESSION", '{"di_token": "fake"}')
    assert remote_auth_unofficial.resume_from_env() is True
    assert remote_auth_unofficial.has_session()


def test_resume_from_env_without_env_var_is_a_noop(monkeypatch):
    monkeypatch.delenv("GARMIN_UNOFFICIAL_SESSION", raising=False)
    assert remote_auth_unofficial.resume_from_env() is False
    assert not remote_auth_unofficial.has_session()


def test_resume_from_env_with_bad_token_is_a_noop(monkeypatch):
    class _FailsToLoad(_FakeGarminNoMFA):
        def login(self, tokenstore: str | None = None) -> tuple[None, None]:
            raise RuntimeError("stale token")

    monkeypatch.setattr(remote_auth_unofficial, "Garmin", _FailsToLoad)
    monkeypatch.setenv("GARMIN_UNOFFICIAL_SESSION", '{"di_token": "stale"}')
    assert remote_auth_unofficial.resume_from_env() is False
    assert not remote_auth_unofficial.has_session()


# --------------------------------------------------------------------------
# /admin/garmin-login page
# --------------------------------------------------------------------------


def test_admin_garmin_login_page_loads(client):
    resp = client.get("/admin/garmin-login")
    assert resp.status_code == 200
    assert "Garmin Connect email" in resp.text


def test_admin_garmin_login_rejects_wrong_access_password(client):
    resp = client.post(
        "/admin/garmin-login",
        data={"access_password": "nope", "garmin_email": "me@example.test", "garmin_password": "hunter2"},
    )
    assert resp.status_code == 401
    assert not remote_auth_unofficial.has_session()


def test_admin_garmin_login_full_flow_enables_unofficial_tool(client, monkeypatch):
    monkeypatch.setattr(remote_auth_unofficial, "Garmin", _FakeGarminNoMFA)

    resp = client.post(
        "/admin/garmin-login",
        data={"access_password": PASSWORD, "garmin_email": "me@example.test", "garmin_password": "hunter2"},
    )
    assert resp.status_code == 200
    assert "Connected" in resp.text
    assert "di_token" in resp.text  # the optional persistence blob (HTML-escaped)
    assert remote_auth_unofficial.has_session()

    # The same session the admin page just established is what the
    # registered tool functions resolve through (see
    # test_unofficial_tool_reports_no_session_before_login for the "before"
    # half of this story).
    assert remote_auth_unofficial.get_client().get_devices() == [{"deviceId": 1234}]


def test_admin_garmin_login_mfa_flow(client, monkeypatch):
    monkeypatch.setattr(remote_auth_unofficial, "Garmin", _FakeGarminMFA)

    start = client.post(
        "/admin/garmin-login",
        data={"access_password": PASSWORD, "garmin_email": "me@example.test", "garmin_password": "hunter2"},
    )
    assert start.status_code == 200
    assert "MFA code" in start.text
    pending_id = start.text.split('name="pending_id" value="')[1].split('"')[0]
    assert pending_id

    wrong = client.post(
        "/admin/garmin-login",
        data={"access_password": PASSWORD, "pending_id": pending_id, "mfa_code": "000000"},
    )
    assert wrong.status_code == 401
    assert not remote_auth_unofficial.has_session()

    right = client.post(
        "/admin/garmin-login",
        data={"access_password": PASSWORD, "pending_id": pending_id, "mfa_code": "123456"},
    )
    assert right.status_code == 200
    assert "Connected" in right.text
    assert remote_auth_unofficial.has_session()


def test_missing_required_env_raises(monkeypatch):
    monkeypatch.delenv("GARMIN_MCP_PUBLIC_URL", raising=False)
    from garmin_mcp.remote_server import _ConfigError, build_server as _build

    with pytest.raises(_ConfigError):
        _build()


# --------------------------------------------------------------------------
# OAuth flow
# --------------------------------------------------------------------------


def test_register_client_returns_credentials(client):
    client_id, client_secret = _register_client(client)
    assert client_id
    assert client_secret


def test_authorize_redirects_to_login(client):
    client_id, _secret = _register_client(client)
    _verifier, challenge = _pkce_pair()
    state_key = _authorize_to_login(client, client_id, challenge)
    assert state_key

    page = client.get(f"/login?state_key={state_key}")
    assert page.status_code == 200
    assert "<form" in page.text


def test_wrong_password_is_rejected_and_code_not_issued(client):
    client_id, _secret = _register_client(client)
    _verifier, challenge = _pkce_pair()
    state_key = _authorize_to_login(client, client_id, challenge)

    resp = client.post("/login", data={"state_key": state_key, "password": "nope"}, follow_redirects=False)
    assert resp.status_code == 401

    # the pending login is still there — a second, correct attempt still works
    result = _login_and_get_code(client, state_key, PASSWORD)
    assert result["code"]


def test_full_login_issues_working_bearer_token(client):
    tokens = _full_login(client)
    resp = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert resp.status_code == 200


def test_mcp_endpoint_rejects_missing_or_garbage_token(client):
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}},
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    no_token = client.post("/", json=init_body, headers=headers)
    assert no_token.status_code == 401

    garbage_token = client.post("/", json=init_body, headers={**headers, "Authorization": "Bearer garbage"})
    assert garbage_token.status_code == 401


def test_authorization_code_is_single_use(client):
    client_id, client_secret = _register_client(client)
    verifier, challenge = _pkce_pair()
    state_key = _authorize_to_login(client, client_id, challenge)
    result = _login_and_get_code(client, state_key, PASSWORD)

    first = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_wrong_pkce_verifier_rejected(client):
    client_id, client_secret = _register_client(client)
    _verifier, challenge = _pkce_pair()
    state_key = _authorize_to_login(client, client_id, challenge)
    result = _login_and_get_code(client, state_key, PASSWORD)

    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": "wrong-verifier",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_refresh_token_rotates_and_old_access_token_stops_working(client):
    tokens = _full_login(client)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}},
    }

    refreshed = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": tokens["client_id"],
            "client_secret": tokens["client_secret"],
        },
    )
    assert refreshed.status_code == 200
    new_access_token = refreshed.json()["access_token"]
    assert new_access_token != tokens["access_token"]

    old_token_call = client.post(
        "/", json=init_body, headers={**headers, "Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert old_token_call.status_code == 401

    new_token_call = client.post(
        "/", json=init_body, headers={**headers, "Authorization": f"Bearer {new_access_token}"}
    )
    assert new_token_call.status_code == 200


def test_spoofed_host_header_is_rejected(client):
    tokens = _full_login(client)
    resp = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}},
        },
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Host": "evil.example.com",
        },
    )
    assert resp.status_code == 421


def test_health_and_metadata_endpoints(client):
    assert client.get("/health").status_code == 200
    assert client.get("/.well-known/oauth-authorization-server").status_code == 200
