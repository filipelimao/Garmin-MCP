"""Local (stdio) MCP server exposing the authenticated user's own Garmin
Connect data — Claude Desktop/Cursor only, never reachable over a network.

This is a **hybrid** server, because Garmin's official Personal API
(launched October 2026) doesn't yet cover everything the unofficial
`garminconnect` library does:

- Most tools call Garmin's own published client,
  `garmin-connect-personal-api-python`
  (https://github.com/garmin/connect-personal-api-python), authenticated
  with a Personal API key you generate yourself — no password, no MFA,
  fully within Garmin's terms of service. See auth_official.py /
  tools_official.py.
- A smaller set of tools — training status/readiness, VO2 max, race
  predictions, personal records, devices, activity splits/laps/weather/
  gear, and respiration — aren't exposed by the official API yet, so
  those fall back to the unofficial `garminconnect`/`garth` library,
  which logs in with your Garmin email/password once via
  `garmin-mcp-auth`. Every such tool's docstring starts with
  "(unofficial API)" so it's always clear which backend is in play. See
  auth_unofficial.py / tools_unofficial.py for the caveats that come with it.

You only need to set up the backend(s) a tool you actually want to use
requires — if you never call an unofficial-API tool, you never need to
run `garmin-mcp-auth` at all.

This stdio server never touches a network port, so it's only reachable
from the machine it runs on. For a remotely-hosted, phone-reachable
server exposing just the official-API tools, see remote_server.py.

Dates are ISO strings, "YYYY-MM-DD"; omitting one defaults to today.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .tools_official import OFFICIAL_TOOLS
from .tools_unofficial import UNOFFICIAL_TOOLS

mcp = MCPServer(
    name="garmin-connect",
    instructions=(
        "Read-only tools for the authenticated user's own Garmin Connect "
        "data. Most tools use Garmin's official Personal API; a smaller "
        "set — each docstring starts with '(unofficial API)' — falls "
        "back to an unofficial library for data Garmin hasn't exposed "
        "officially yet (training status/readiness, VO2 max, race "
        "predictions, personal records, devices, activity splits/laps/"
        "weather/gear, respiration). Dates are ISO 'YYYY-MM-DD' strings; "
        "omit a date argument to default to today. If a tool returns an "
        "{'error': ...} object mentioning a missing key/session, tell "
        "the user to run `garmin-mcp-save-key` (official tools) or "
        "`garmin-mcp-auth` (unofficial tools) and try again."
    ),
)

for _fn in OFFICIAL_TOOLS:
    mcp.add_tool(_fn)
for _fn in UNOFFICIAL_TOOLS:
    mcp.add_tool(_fn)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
