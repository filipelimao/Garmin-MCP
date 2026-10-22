# garmin-connect-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes **your own**
Garmin Connect activity, health, and training data as tools Claude (or
Cursor, or any other MCP client) can call.

## ⚠️ Before you use this

Garmin does not offer a public API for individual developers to read their
own account's data. There is no sanctioned, OAuth-style "Connect with
Garmin" flow available here. This server uses
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect), a
well-known **unofficial** library that logs in the same way the Garmin
Connect website does. That means:

- It's outside Garmin's terms of service, even though it's just reading
  your own data. In practice this is widely used and low-risk, but it
  isn't zero-risk — Garmin could change its login flow or, in principle,
  act on an account for automated access.
- `garminconnect` is itself built on top of
  [`garth`](https://github.com/matin/garth), whose docs currently say it
  is **deprecated and no longer actively maintained**, and that new
  logins may break if Garmin changes its auth flow. If login ever stops
  working, that's the most likely reason — check the
  [python-garminconnect issues](https://github.com/cyberjunky/python-garminconnect/issues)
  before assuming it's something in this repo.
- Your Garmin password is used once, interactively, by the `garmin-mcp-auth`
  setup script below — never by the MCP server itself. After that, a
  session token is cached to disk (`~/.garminconnect` by default). **Treat
  that file like a password.** Anyone with it can read your Garmin data
  until it expires or you revoke it.

If that's not a trade-off you want, the lower-risk alternatives are: (a)
Garmin's "Export Your Data" feature for a one-off, non-live file dump, or
(b) if your activities already sync to Strava, use Strava's own official
API instead.

## What this gives Claude

24 read-only tools, grouped into three areas:

| Area | Tools |
|---|---|
| **Activities & workouts** | `list_activities`, `list_activities_by_date`, `get_last_activity`, `get_activity`, `get_activity_details`, `get_activity_splits`, `get_activity_laps`, `get_activity_weather`, `get_activity_gear`, `get_personal_records`, `list_devices` |
| **Health & wellness** | `get_daily_summary`, `get_sleep`, `get_heart_rate`, `get_resting_heart_rate`, `get_body_battery`, `get_stress`, `get_steps`, `get_respiration`, `get_spo2` |
| **Training metrics** | `get_training_status`, `get_training_readiness`, `get_max_metrics` (VO2 max), `get_hrv`, `get_race_predictions` |

Every tool is read-only — nothing here can modify your Garmin account.

## Setup

Requires Python 3.10+.

```bash
git clone <this-repo-url>
cd garmin-connect-mcp
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

### 1. Log in once, interactively

From a real terminal — **not** from inside Claude or Cursor, since you may
need to type an MFA code:

```bash
garmin-mcp-auth
```

This asks for your Garmin Connect email and password (and an MFA code, if
your account uses one), then caches a session token — by default at
`~/.garminconnect`. You only need to do this once; the token refreshes
itself automatically as it's used, and commonly lasts close to a year.

If it ever stops working, just run `garmin-mcp-auth` again. To delete the
cached session instead of logging in, run `garmin-mcp-auth --logout`.

### 2. Point your MCP client at the server

**Claude Desktop** — edit your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "garmin-connect": {
      "command": "/absolute/path/to/garmin-connect-mcp/.venv/bin/garmin-mcp-server"
    }
  }
}
```

**Cursor** — edit `.cursor/mcp.json` (project-level or `~/.cursor/mcp.json`
for all projects):

```json
{
  "mcpServers": {
    "garmin-connect": {
      "command": "/absolute/path/to/garmin-connect-mcp/.venv/bin/garmin-mcp-server"
    }
  }
}
```

Replace `/absolute/path/to/garmin-connect-mcp` with wherever you cloned
this repo. Restart the client afterward so it picks up the new server.

### 3. Use a different token location (optional)

By default the token cache lives at `~/.garminconnect`. To use a different
path, set `GARMINTOKENS` before running either `garmin-mcp-auth` or the
server — e.g. add it to the `env` block of the MCP client config above,
and export it in your shell before running `garmin-mcp-auth`.

## How it works

- `garmin_mcp/authenticate.py` — the one-time interactive login script
  (`garmin-mcp-auth`). Only this file ever touches your email/password.
- `garmin_mcp/auth.py` — resumes the cached session token for the running
  server. Returns a clear error (as a normal tool result, not a crash) if
  no valid session is cached yet.
- `garmin_mcp/server.py` — the MCP server itself: one `async` tool per
  Garmin data type, each running the underlying blocking
  `garminconnect` call in a thread so the server stays responsive.

## Troubleshooting

- **A tool returns `{"error": "No cached Garmin Connect session found..."}`**
  — run `garmin-mcp-auth`.
- **A tool returns `{"error": "...session..."}`** mentioning expiry —
  the cached token was rejected; run `garmin-mcp-auth` again to refresh it.
- **Login itself fails** — check the
  [python-garminconnect issue tracker](https://github.com/cyberjunky/python-garminconnect/issues)
  for recent reports; Garmin's login flow has changed before and will
  likely change again.

## License

MIT — see [LICENSE](LICENSE).
