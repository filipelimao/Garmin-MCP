"""Session management for Garmin's *official* Connect Personal API.

Unlike auth_unofficial.py, nothing here ever touches a Garmin password.
It only uses a Personal API key you generate yourself from Garmin
Connect's website (Settings -> Personal API Key -> Create Key) and save
locally with `garmin-mcp-save-key` (see authenticate_official.py).

This wraps Garmin's own published client,
`garmin-connect-personal-api-python` (https://github.com/garmin/connect-personal-api-python),
which handles the API-key-to-bearer-token exchange and caches the token
itself (~/.config/garmin/connect-personal-api-bearer-token.json by
default). Access tokens are short-lived (on the order of 24 hours) and
there are no refresh tokens, so a long-running MCP server process has to
notice a 401 and re-authenticate — that's what _call() in server.py does
by calling reset_client() and retrying once.
"""

from __future__ import annotations

import threading

from garmin_personal_api import (
    GarminPersonalApiClient,
    GarminPersonalApiError,
    PersonalApiAuthenticator,
    resolve_api_key,
)

__all__ = ["OfficialAuthError", "get_client", "reset_client"]


class OfficialAuthError(RuntimeError):
    """Raised when there is no usable Garmin Personal API key or token."""


_client: GarminPersonalApiClient | None = None
_lock = threading.Lock()


def get_client() -> GarminPersonalApiClient:
    """Return a GarminPersonalApiClient, authenticating if needed.

    Raises OfficialAuthError with a message telling the user how to fix
    it if no Personal API key is saved yet, or if exchanging it for an
    access token fails (key deleted/expired on Garmin's side, network
    issue, etc.).
    """
    global _client

    with _lock:
        if _client is not None:
            return _client

        try:
            api_key = resolve_api_key()
        except GarminPersonalApiError as exc:
            raise OfficialAuthError(
                "No Garmin Personal API key found. Create one at Garmin "
                "Connect's website (Settings -> Personal API Key -> Create "
                "Key), then run `garmin-mcp-save-key` once from a terminal "
                "to save it, then restart this MCP server."
            ) from exc

        try:
            with PersonalApiAuthenticator() as authenticator:
                token = authenticator.obtain_token(api_key)
        except Exception as exc:  # noqa: BLE001 - surface any failure clearly
            raise OfficialAuthError(
                "Could not get a Garmin Personal API access token "
                f"({type(exc).__name__}: {exc}). The saved key may have "
                "expired or been deleted on Garmin's side — create a new "
                "one and run `garmin-mcp-save-key` again."
            ) from exc

        _client = GarminPersonalApiClient(token.access_token)
        return _client


def reset_client() -> None:
    """Drop the in-memory client so the next call re-authenticates.

    Used after a 401 (access token expired/revoked) so the next request
    gets a fresh token instead of repeating the same failing call.
    """
    global _client
    with _lock:
        if _client is not None:
            _client.close()
        _client = None
