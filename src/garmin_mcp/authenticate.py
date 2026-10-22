"""One-time interactive Garmin Connect login.

Run this once from a terminal (not from Claude or Cursor) before starting
the MCP server:

    garmin-mcp-auth

It logs in with your Garmin Connect email and password (prompting for an
MFA code if your account uses one), then caches a session token to disk
(~/.garminconnect by default, or $GARMINTOKENS). The MCP server
(garmin-mcp-server) only ever reads that cached token — it never sees your
password.

The cached token is normally good for a long time (commonly close to a
year) and is refreshed automatically as it's used. If it ever expires or
is revoked, just run `garmin-mcp-auth` again.

Treat the cached token file like a password: anyone who has it can read
your Garmin Connect data. `garmin-mcp-auth --logout` deletes the local
copy (it does not revoke it on Garmin's servers — do that from your
Garmin Connect account security settings if the token may have leaked).
"""

from __future__ import annotations

import argparse
import os
import sys
from getpass import getpass

from garminconnect import Garmin

from .auth import DEFAULT_TOKENSTORE


def _ask_mfa_code() -> str:
    return input("Enter the MFA code Garmin just sent you: ").strip()


def _login(tokenstore: str) -> None:
    email = os.environ.get("GARMIN_EMAIL") or input("Garmin Connect email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass("Garmin Connect password: ")

    print(f"Logging in to Garmin Connect as {email} ...")
    client = Garmin(email, password, prompt_mfa=_ask_mfa_code)
    try:
        client.login(tokenstore)
    except Exception as exc:  # noqa: BLE001
        print(f"Login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Logged in. Session cached at {tokenstore}.")
    print("You can now start the MCP server without re-entering credentials.")


def _logout(tokenstore: str) -> None:
    Garmin().logout(tokenstore)
    print(f"Removed the cached session at {tokenstore}.")
    print(
        "Note: this only deletes the local copy. If the token may have "
        "leaked, also revoke it from your Garmin Connect account security "
        "settings."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Delete the locally cached session instead of logging in.",
    )
    parser.add_argument(
        "--tokenstore",
        default=DEFAULT_TOKENSTORE,
        help=f"Where to store/read the cached session (default: {DEFAULT_TOKENSTORE}).",
    )
    args = parser.parse_args()

    if args.logout:
        _logout(args.tokenstore)
    else:
        _login(args.tokenstore)


if __name__ == "__main__":
    main()
