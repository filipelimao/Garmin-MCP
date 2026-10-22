"""Session management for the Garmin Connect MCP server.

The running MCP server never asks for a Garmin email or password. It only
ever *resumes* a session token that was cached earlier by the one-time
`garmin-mcp-auth` script (see authenticate.py). This keeps credentials out
of the server process, out of MCP client config files, and out of any logs
the server itself produces.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from garminconnect import Garmin

#: Where the cached session token lives. Matches garminconnect's own
#: GARMINTOKENS convention so garmin-mcp-auth and garmin-mcp-server agree
#: on the same location by default.
DEFAULT_TOKENSTORE = os.environ.get("GARMINTOKENS") or str(Path.home() / ".garminconnect")


class GarminAuthError(RuntimeError):
    """Raised when there is no usable cached Garmin Connect session."""


_client: Garmin | None = None
_lock = threading.Lock()


def _tokenstore_path() -> Path:
    return Path(DEFAULT_TOKENSTORE).expanduser()


def get_client() -> Garmin:
    """Return a logged-in Garmin client, resuming the cached session.

    Raises GarminAuthError with a message telling the user how to fix it
    if no cached session exists yet, or if the cached session has expired
    and can't be silently renewed.
    """
    global _client

    with _lock:
        if _client is not None:
            return _client

        tokenstore = _tokenstore_path()
        if not tokenstore.exists():
            raise GarminAuthError(
                "No cached Garmin Connect session found at "
                f"{tokenstore}. Run `garmin-mcp-auth` once from a terminal "
                "to log in (you'll be asked for your Garmin email, "
                "password, and an MFA code if your account uses one), "
                "then restart this MCP server."
            )

        client = Garmin()
        try:
            client.login(str(tokenstore))
        except Exception as exc:  # noqa: BLE001 - surface any failure clearly
            raise GarminAuthError(
                "Could not resume the cached Garmin Connect session "
                f"({type(exc).__name__}: {exc}). The cached token may have "
                "expired or been revoked. Run `garmin-mcp-auth` again to "
                "log in and refresh it."
            ) from exc

        _client = client
        return _client


def reset_client() -> None:
    """Drop the in-memory client so the next call re-reads the tokenstore.

    Mainly useful for tests, or if a caller wants to force a fresh resume
    after re-running garmin-mcp-auth without restarting the server.
    """
    global _client
    with _lock:
        _client = None
