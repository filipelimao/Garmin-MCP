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
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from garmin_mcp.remote_server import build_server

PUBLIC_URL = "https://garmin-mcp.example.test"
PASSWORD = "correct-horse-battery-staple"
REDIRECT_URI = "https://claude.ai/api/mcp/callback"


@pytest.fixture
def remote_env(monkeypatch):
    monkeypatch.setenv("GARMIN_MCP_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("GARMIN_MCP_ACCESS_PASSWORD", PASSWORD)
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "dummy-key-for-tests")


@pytest.fixture
def app(remote_env):
    mcp, transport_security = build_server()
    return mcp, mcp.streamable_http_app(transport_security=transport_security)


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
async def test_remote_server_only_registers_official_tools(app):
    mcp, _starlette_app = app
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert len(names) == 23
    # none of the unofficial-fallback tools are present
    assert "get_training_status" not in names
    assert "get_max_metrics" not in names


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
        "/mcp",
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

    no_token = client.post("/mcp", json=init_body, headers=headers)
    assert no_token.status_code == 401

    garbage_token = client.post("/mcp", json=init_body, headers={**headers, "Authorization": "Bearer garbage"})
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
        "/mcp", json=init_body, headers={**headers, "Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert old_token_call.status_code == 401

    new_token_call = client.post(
        "/mcp", json=init_body, headers={**headers, "Authorization": f"Bearer {new_access_token}"}
    )
    assert new_token_call.status_code == 200


def test_spoofed_host_header_is_rejected(client):
    tokens = _full_login(client)
    resp = client.post(
        "/mcp",
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
