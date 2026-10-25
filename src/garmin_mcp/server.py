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
- Workout creation/scheduling/push (create a structured workout, list/get/
  delete one, schedule it on the calendar, push it to a device) also
  isn't covered by the official Personal API, so it uses the same
  unofficial session as above. Unlike every other tool here, these write
  to the account rather than just reading it. See tools_workouts.py.

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

from . import auth_unofficial
from .tools_official import OFFICIAL_TOOLS
from .tools_unofficial import build_unofficial_tools
from .tools_workouts import build_workout_tools

#: Bound to the local, file-cached session (see auth_unofficial.py). The
#: remote server builds its own copy of these same tools bound to a
#: different, in-memory session instead — see remote_server.py.
UNOFFICIAL_TOOLS = build_unofficial_tools(auth_unofficial.get_client)

#: Workout creation/scheduling/push tools (see tools_workouts.py) — same
#: unofficial-API session as UNOFFICIAL_TOOLS above, kept in their own
#: module since they write to the account rather than just reading it.
WORKOUT_TOOLS = build_workout_tools(auth_unofficial.get_client)

mcp = MCPServer(
    name="garmin-connect",
    instructions=(
        "Read-only tools for the authenticated user's own Garmin Connect "
        "data. Most tools use Garmin's official Personal API; a smaller "
        "set — each docstring starts with '(unofficial API)' — falls "
        "back to an unofficial library for data Garmin hasn't exposed "
        "officially yet (training status/readiness, VO2 max, race "
        "predictions, personal records, devices, activity splits/laps/"
        "weather/gear, respiration), plus a set of workout tools "
        "(create/list/get/delete a structured workout, schedule it, push "
        "it to a device) that write to the account rather than just "
        "reading it. Dates are ISO 'YYYY-MM-DD' strings; omit a date "
        "argument to default to today. If a tool returns an "
        "{'error': ...} object mentioning a missing key/session, tell "
        "the user to run `garmin-mcp-save-key` (official tools) or "
        "`garmin-mcp-auth` (unofficial/workout tools) and try again."
    ),
)

for _fn in OFFICIAL_TOOLS:
    mcp.add_tool(_fn)
for _fn in UNOFFICIAL_TOOLS:
    mcp.add_tool(_fn)
for _fn in WORKOUT_TOOLS:
    mcp.add_tool(_fn)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
