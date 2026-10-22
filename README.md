# garmin-connect-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes **your own**
Garmin Connect activity, health, and training data as tools Claude (or
Cursor, or any other MCP client) can call.

It's a **hybrid** server:

- Most tools call Garmin's own official **Personal API**, launched
  October 2026 — you generate a key yourself from Garmin Connect's
  website, no password ever touches this code, and it's fully within
  Garmin's terms of service.
- A smaller set of tools fall back to the **unofficial**
  [`garminconnect`](https://github.com/cyberjunky/python-garminconnect)
  library, for data Garmin hasn't exposed officially yet (see below).
  Every one of those tools' descriptions starts with `(unofficial API)`
  so it's always clear which backend answered.

You only need to set up the backend(s) behind the tools you actually
want — if you never call an unofficial-API tool, you never need to run
`garmin-mcp-auth` or hand this code your Garmin password at all.

## What this gives Claude

**23 tools on the official Personal API** — no password, ever:

| Area | Tools |
|---|---|
| **Activities & workouts** | `list_activities`, `get_activity`, `get_activity_workouts`, `get_wellness_activity_summaries` |
| **Health & wellness** | `get_daily_summary`, `get_sleep`, `get_sleep_summaries`, `get_steps`, `get_heart_rate`, `get_heart_rate_zones`, `get_body_battery`, `get_stress`, `get_hrv`, `get_hrv_summaries`, `get_spo2`, `get_fitness_age`, `get_hydration`, `get_weight_measurements`, `get_weight_goals`, `get_food_logs`, `get_profile_settings` |
| **Women's health** | `get_menstrual_cycle_summary`, `get_menstrual_cycle_day_view` |

**11 tools on the unofficial fallback** — data Garmin's Personal API
doesn't cover yet, each clearly labeled `(unofficial API)` in its
description:

| Area | Tools |
|---|---|
| **Training metrics** | `get_training_status`, `get_training_readiness`, `get_max_metrics` (VO2 max), `get_race_predictions` |
| **Activities & workouts** | `get_personal_records`, `list_devices`, `get_activity_splits`, `get_activity_laps`, `get_activity_weather`, `get_activity_gear` |
| **Health & wellness** | `get_respiration` |

Every tool is read-only — nothing here can modify your Garmin account.
(The official Personal API is pull/read-only by design; it also can't
push structured workouts to your watch — that's a Business-tier-only
capability Garmin calls the Training API.)

## ⚠️ About the unofficial fallback tools

The 11 tools above are outside Garmin's terms of service, even though
they're just reading your own data — Garmin hasn't exposed this data via
an official API yet. In practice this is widely used and low-risk, but
not zero-risk. They use
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect),
itself built on [`garth`](https://github.com/matin/garth), whose docs
currently say it's **deprecated and no longer actively maintained** —
new logins may break if Garmin changes its login flow. If one of these
specific tools stops working, check the
[python-garminconnect issues](https://github.com/cyberjunky/python-garminconnect/issues)
before assuming it's something in this repo. As Garmin expands the
official Personal API, expect these tools to migrate over and this
section to shrink.

If you'd rather skip this entirely: just don't run `garmin-mcp-auth`.
The 23 official tools work completely independently of it; the 11
unofficial tools will return a clear `{"error": ...}` telling you (or
Claude) that they need it, instead of failing silently.

## Setup

Requires Python 3.10+ (the official Garmin package needs 3.12+; if
you're on 3.10 or 3.11 you can still use the unofficial-fallback tools,
but install the official dependency separately once you upgrade).

```bash
git clone <this-repo-url>
cd garmin-connect-mcp
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

### 1. Official Personal API — generate a key (no password needed)

1. Sign in to [Garmin Connect](https://connect.garmin.com) on the web.
2. Go to **Settings → Personal API Key → Create Key**. Pick an
   expiration (30/90/180 days, or 1 year). Garmin shows the key exactly
   once — copy it.
3. Save it locally:

   ```bash
   garmin-mcp-save-key
   ```

   Paste the key when prompted. It's written to
   `~/.config/garmin/connect-personal-api-key` with `0600` permissions.
   (Setting the `GARMIN_PERSONAL_API_KEY` environment variable instead
   also works and takes precedence — `garmin-mcp-save-key` just avoids
   putting the key in your shell history.)

Garmin emails you about a week before the key expires; when it does,
repeat these steps with a new key.

### 2. Unofficial fallback — only if you want those 11 tools

From a real terminal — **not** from inside Claude or Cursor, since you
may need to type an MFA code:

```bash
garmin-mcp-auth
```

Asks for your Garmin Connect email and password (and an MFA code, if
your account uses one), then caches a session token at
`~/.garminconnect`. You only need to do this once; the token refreshes
itself automatically as it's used, and commonly lasts close to a year.
If it ever stops working, run `garmin-mcp-auth` again. To delete the
cached session instead, run `garmin-mcp-auth --logout`.

### 3. Point your MCP client at the server

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

**Cursor** — edit `.cursor/mcp.json` (project-level or
`~/.cursor/mcp.json` for all projects): same JSON as above.

Replace `/absolute/path/to/garmin-connect-mcp` with wherever you cloned
this repo. Restart the client afterward so it picks up the new server.

## Testing without touching your real Garmin account

```bash
pip install -e ".[test]"
pytest
```

The test suite (`tests/test_server.py`) mocks out both backends
entirely — it never logs in or hits a real API. It checks that every
tool calls the right underlying method with the right arguments, that a
missing key/session comes back as a normal `{"error": ...}` result
instead of crashing the server, that an expired official-API access
token is transparently refreshed and retried once, and that all 34
tools register with the official ones unlabeled and the unofficial ones
all starting with `(unofficial API)`. It's a fast sanity check — it does
**not** replace actually running the setup steps above and trying a
real tool call from Claude or Cursor.

## How it works

- `garmin_mcp/auth_official.py` / `authenticate_official.py` — the
  official Personal API path. `garmin-mcp-save-key` only ever saves a
  key you already generated; the running server exchanges it for a
  short-lived bearer token (via Garmin's own
  [`garmin-connect-personal-api-python`](https://github.com/garmin/connect-personal-api-python)
  client) and transparently re-authenticates if a token expires mid-session.
- `garmin_mcp/auth_unofficial.py` / `authenticate_unofficial.py` — the
  unofficial fallback path. Only `authenticate_unofficial.py` (run via
  `garmin-mcp-auth`) ever touches your email/password; the server itself
  only ever resumes the cached session token.
- `garmin_mcp/server.py` — the MCP server: one `async` tool per Garmin
  data type, each running its blocking API call in a thread so the
  server stays responsive, and each clearly routed to the official or
  unofficial backend.

## Troubleshooting

- **A tool returns `{"error": "No Garmin Personal API key found..."}`**
  — run `garmin-mcp-save-key`.
- **A tool returns `{"error": "...access token..."}`** — the saved key
  expired or was deleted on Garmin's side; create a new one and run
  `garmin-mcp-save-key` again.
- **A tool returns `{"error": "No cached Garmin Connect session found..."}`**
  — run `garmin-mcp-auth` (only needed for the 11 unofficial-fallback tools).
- **An unofficial-fallback tool's login fails** — check the
  [python-garminconnect issue tracker](https://github.com/cyberjunky/python-garminconnect/issues);
  Garmin's login flow has changed before and will likely change again.
- **Some fields seem missing from official-API activity data** — there
  have been reports (e.g. from Runalyze) that Garmin strips some
  undocumented fields (VO2 max estimate, recovery time, performance
  condition) from API-delivered files. That's a Garmin-side limitation
  of the official API, not a bug here — it's part of why
  `get_max_metrics` still uses the unofficial fallback.

## License

MIT — see [LICENSE](LICENSE).
