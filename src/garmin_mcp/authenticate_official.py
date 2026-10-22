"""One-time save of your Garmin Connect Personal API key.

Unlike `garmin-mcp-auth` (the *unofficial*-library login), this never
asks for your Garmin password, never prompts for an MFA code, and never
logs in as you. All it does is save a key you already created yourself.

Steps:

1. Sign in to Garmin Connect on the web (connect.garmin.com).
2. Go to Settings -> Personal API Key -> Create Key. Pick an expiration
   (30/90/180 days or 1 year). Garmin shows the key exactly once.
3. Run this script and paste that key in when prompted:

       garmin-mcp-save-key

It's saved to ~/.config/garmin/connect-personal-api-key (0600
permissions) by default, which is where both the MCP server and
Garmin's own `garmin-connect-personal-api-python` package look for it.
Setting GARMIN_PERSONAL_API_KEY in your environment instead works too
and takes precedence — this script is just a convenience so the key
never has to touch your shell history.

Treat this key like a password: anyone who has it can read your Garmin
Connect data until it expires or you delete it from Garmin Connect's
Personal API Key settings page.
"""

from __future__ import annotations

import os
from getpass import getpass
from pathlib import Path

from garmin_personal_api import DEFAULT_API_KEY_FILE


def main() -> None:
    key = getpass("Paste your Garmin Connect Personal API key: ").strip()
    if not key:
        print("No key entered — nothing saved.")
        return

    path = Path(DEFAULT_API_KEY_FILE).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    os.chmod(path, 0o600)

    print(f"Saved. The MCP server will read it from {path}.")
    print("You can now start the MCP server without re-entering the key.")


if __name__ == "__main__":
    main()
