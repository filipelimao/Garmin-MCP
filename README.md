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

**11 read-only tools on the unofficial fallback** — data Garmin's
Personal API doesn't cover yet, each clearly labeled `(unofficial API)`
in its description:

| Area | Tools |
|---|---|
| **Training metrics** | `get_training_status`, `get_training_readiness`, `get_max_metrics` (VO2 max), `get_race_predictions` |
| **Activities & workouts** | `get_personal_records`, `list_devices`, `get_activity_splits`, `get_activity_laps`, `get_activity_weather`, `get_activity_gear` |
| **Health & wellness** | `get_respiration` |

**9 workout tools, also on the unofficial fallback** — these *write* to
your account (create, schedule, push, or delete a structured workout), so
they're kept separate from the read-only tools above, both in code
(`tools_workouts.py`) and in this table:

| Area | Tools |
|---|---|
| **Build a workout** | `create_simple_interval_workout` (warmup/interval-repeat/cooldown, no JSON needed), `create_workout` (fully custom, raw JSON), `get_workout_types` (discover the step/target/sport ids `create_workout` needs) |
| **Manage the library** | `list_workouts`, `get_workout`, `delete_workout` |
| **Get it onto your watch** | `schedule_workout` (put it on the calendar for your next normal sync), `push_workout_to_device` (push one workout right now), `send_scheduled_workouts_to_devices` (push everything scheduled right now) |

Everything else in this project is read-only. The official Personal API
is pull/read-only by design and doesn't cover workouts at all yet — as of
Garmin's October 2026 API revamp, pushing structured workouts is part of
a separate, free **Startup** developer tier (Courses & Training APIs,
up to 1,000 users) that would need its own application process. The 9
workout tools above sidestep that by using the same unofficial session as
the read-only fallback tools, which already supports workout upload,
scheduling, and device push today.

## ⚠️ About the unofficial fallback and workout tools

The 20 tools above (11 read-only + 9 workout) are outside Garmin's terms
of service, even the read-only ones — Garmin hasn't exposed this data via
an official API yet. In practice this is widely used and low-risk, but
not zero-risk. They use
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect),
itself built on [`garth`](https://github.com/matin/garth), whose docs
currently say it's **deprecated and no longer actively maintained** —
new logins may break if Garmin changes its login flow. If one of these
specific tools stops working, check the
[python-garminconnect issues](https://github.com/cyberjunky/python-garminconnect/issues)
before assuming it's something in this repo. As Garmin expands the
official Personal API, expect the read-only tools to migrate over and
that part of this section to shrink.

The workout tools carry an extra risk beyond "unofficial API": they
write to your real Garmin account. `create_workout`/
`create_simple_interval_workout` add to your actual workout library,
`delete_workout` permanently removes one, and `push_workout_to_device`/
`send_scheduled_workouts_to_devices` send data to your real watch. There's
no sandbox/test-account mode — try a throwaway workout name first if
you're unsure, and check `list_workouts`/`get_workout` before deleting
anything.

If you'd rather skip all of this: just don't run `garmin-mcp-auth`
(local server) or visit `/admin/garmin-login` (remote server). The 23
official tools work completely independently of it; the 20 unofficial
tools will return a clear `{"error": ...}` telling you (or Claude) that
they need it, instead of failing silently.

## Two ways to run this

| | `garmin-mcp-server` (local) | `garmin-mcp-remote-server` (remote) |
|---|---|---|
| Transport | stdio | HTTP (Streamable HTTP) |
| Where it runs | your own machine | anywhere public (Railway, etc.) |
| Reachable from | Claude Desktop/Cursor on that machine only | claude.ai on **any device**, including your phone |
| Tools | all 43 (23 official + 11 unofficial + 9 workout) | 23 official always; the 20 unofficial/workout tools are *registered* but only work once you opt in at `/admin/garmin-login` |
| Your Garmin password | only touches `garmin-mcp-auth`, run by hand | **never** — typed into `/admin/garmin-login` directly, never into Claude or an env var |
| Access control | whoever has your machine | OAuth2 + a password you set (`GARMIN_MCP_ACCESS_PASSWORD`) |

Use the local server if you only ever use Claude Desktop/Cursor on one
machine and want every tool with the least setup — the unofficial and
workout tools work as soon as you run `garmin-mcp-auth` once. Use the
remote server if you want this to work from claude.ai on your phone too;
the 23 official tools work there with no extra risk, and the unofficial
read-only tools (training status/readiness, VO2 max, race predictions,
personal records, devices, activity splits/laps/weather/gear,
respiration) plus the 9 workout tools are available too, but opting into
them means your Garmin login touches a public host once — see
[Unofficial fallback tools on the remote server](#unofficial-fallback-tools-on-the-remote-server-optional-higher-risk)
below before deciding. Nothing stops you running both servers at once.

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

### 2. Unofficial fallback — only if you want those 20 tools (11 read-only + 9 workout)

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

## Remote server (phone-reachable)

This deploys `garmin-mcp-remote-server` — the HTTP variant, with the 23
official tools always on and the unofficial/workout tools available as
an opt-in (see below) — somewhere public, then adds it to claude.ai as a
custom connector so it works from any device, phone included.

### 1. Deploy to Railway

1. Push this repo to GitHub (it already has a `Dockerfile` Railway will
   auto-detect — no other config needed).
2. In [Railway](https://railway.app), **New Project → Deploy from GitHub
   repo** → pick this repo.
3. Under the service's **Variables**, set:
   - `GARMIN_PERSONAL_API_KEY` — your Garmin Personal API key (same key
     as the local setup's step 1, pasted directly as a secret env var —
     there's no terminal here to run `garmin-mcp-save-key`).
   - `GARMIN_MCP_ACCESS_PASSWORD` — a password *you* invent for this
     server. It's unrelated to your Garmin password and this server
     never sees your Garmin password at all; this is the one secret that
     decides who can get a token and call your tools.
   - `GARMIN_MCP_PUBLIC_URL` — leave this for step 4, once Railway has
     assigned a domain.
4. Railway builds and deploys; under **Settings → Networking**, click
   **Generate Domain** to get a public HTTPS URL like
   `https://garmin-mcp-production.up.railway.app`.
5. Go back to **Variables** and set `GARMIN_MCP_PUBLIC_URL` to that exact
   URL (no trailing slash). Redeploy (Railway usually does this
   automatically when a variable changes).
6. Sanity check: `curl https://<your-domain>/health` should return `ok`.

Garmin emails you about a week before your Personal API key expires;
when it does, generate a new one and update the
`GARMIN_PERSONAL_API_KEY` variable in Railway.

### 2. Add it to claude.ai as a custom connector

1. On claude.ai: **Settings → Connectors → Add custom connector**.
2. Paste your Railway URL as the connector URL.
3. Pick **Register automatically** for the OAuth client setup (Dynamic
   Client Registration) — the server handles this itself, no manual
   client ID/secret to create.
4. Connecting will open a small login page asking for the access
   password — the `GARMIN_MCP_ACCESS_PASSWORD` you set in Railway, not
   your Garmin password. Enter it once; claude.ai stores the resulting
   token and refreshes it automatically afterward.
5. Add the connector to whichever Projects should use it (e.g. your
   cycling coach Project) under that Project's connector settings.

That's it — the 23 official-API tools are now reachable from claude.ai
on web, desktop, and mobile.

### How the login works, briefly

There's no user database — you're the only real user. claude.ai
registers itself as an OAuth client automatically (that's normal and
expected; registering isn't the same as getting access). Getting an
actual access token still requires your `GARMIN_MCP_ACCESS_PASSWORD` via
the login page. Tokens and registered clients are kept in memory, not in
a database, so a Railway restart or redeploy means reconnecting once
(claude.ai will prompt you) — there's nothing durable to go stale or get
out of sync.

### Unofficial fallback tools on the remote server (optional, higher risk)

The 11 unofficial-fallback tools and the 9 workout tools (see the warning
above) are *registered* on the remote server too, but return
`{"error": ...}` until you log in to Garmin's unofficial API there —
deliberately a separate, opt-in step from deploying the server itself,
because it means your Garmin email/password exist on a public host at
least once, which is a meaningfully bigger risk than the
official-API-only setup above. Think it through before doing this; a
compromise of this server then means full Garmin account access —
including the ability to create, push, and delete workouts on your real
account — not just read-only fitness data. If you'd rather not, skip this
section — everything else works exactly as described above.

If you do want it:

1. Visit `https://<your-domain>/admin/garmin-login`.
2. Enter your `GARMIN_MCP_ACCESS_PASSWORD` (the same one from step 3
   above — not your Garmin password) plus your Garmin email and
   password. These go straight to this server over HTTPS, never through
   Claude or claude.ai, and the server never writes the password to
   disk, an env var, or a log line — only the resulting session is kept,
   in memory.
3. If Garmin challenges the login with an MFA code (likely, the first
   time, since a cloud host's IP looks new to it), you'll get a second
   form for the code it emailed or pushed to your Garmin Connect app.
4. On success, the page shows an opaque session token. Memory-only means
   a restart, redeploy, or (on Render's free tier) idle-spindown forgets
   it and you'll need to revisit this page — if that's annoying, paste
   that token into a `GARMIN_UNOFFICIAL_SESSION` environment variable and
   redeploy; the server will resume it automatically from then on,
   without needing your password again. Treat that token like a
   password too: whoever has it can read your Garmin data.

## Testing without touching your real Garmin account

```bash
pip install -e ".[test]"
pytest
```

The test suite mocks out both backends entirely — it never logs in or
hits a real API, Garmin or claude.ai. Two files:

- `tests/test_server.py` — the local hybrid server. Checks that every
  tool calls the right underlying method with the right arguments, that
  a missing key/session comes back as a normal `{"error": ...}` result
  instead of crashing the server, that an expired official-API access
  token is transparently refreshed and retried once, and that all 43
  tools register with the official ones unlabeled and the unofficial/
  workout ones all starting with `(unofficial API)`.
- `tests/test_tools_workouts.py` — the pure workout-JSON builder behind
  `create_simple_interval_workout` (no network calls at all): every
  validation error, and the exact shape of the built workout for
  duration- vs. distance-based intervals, each target type, and the
  warmup/recovery/cooldown steps being present or omitted correctly.
- `tests/test_remote_server.py` — the remote server's OAuth flow, driven
  through the real Starlette app (not just the provider in isolation):
  dynamic client registration, the password-gated login page (including
  a wrong-password attempt), PKCE code exchange, that an authorization
  code can't be redeemed twice, refresh-token rotation invalidating the
  old access token, bearer-token enforcement on the MCP endpoint, Host-
  header validation, that all 43 tools are registered (23 official + 11
  unofficial + 9 workout), and that the unofficial/workout ones report a
  clear "no session" error until `/admin/garmin-login` succeeds.

It's a fast sanity check — it does **not** replace actually running the
setup steps above and trying a real tool call from Claude, Cursor, or
claude.ai.

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
  only ever resumes the cached session token. Not imported by the remote
  server at all.
- `garmin_mcp/tools_official.py` / `tools_unofficial.py` /
  `tools_workouts.py` — the 23, 11, and 9 tool implementations, as plain
  functions rather than tied to one server instance, so the local and
  remote servers can register the same tool code (`mcp.add_tool(fn)`)
  instead of maintaining two copies. `tools_unofficial.py` and
  `tools_workouts.py` are each a factory, `build_*_tools(get_client)`,
  so the same functions can be bound to either session source below;
  `tools_workouts.py` is kept separate from `tools_unofficial.py`
  because it writes to the account instead of just reading it.
- `garmin_mcp/server.py` — the local, stdio MCP server: registers all 43
  tools from the three modules above, the unofficial/workout ones bound
  to `auth_unofficial.get_client` (the file-cached session from
  `garmin-mcp-auth`). Reachable only from the machine it runs on.
- `garmin_mcp/remote_auth.py` / `remote_auth_unofficial.py` /
  `remote_server.py` — the remote, HTTP MCP server: always registers the
  23 official tools; also registers the 11 unofficial and 9 workout
  tools, bound instead to `remote_auth_unofficial.get_client` (an
  in-memory session established at the owner-only `/admin/garmin-login`
  page, never a cached file or an env var holding your password) — plus
  a minimal single-user OAuth 2.0 authorization server (Dynamic Client
  Registration + a password-gated `/login` page) so a bearer token, not
  just the URL, is what lets a client call any of them.

## Troubleshooting

- **A tool returns `{"error": "No Garmin Personal API key found..."}`**
  — run `garmin-mcp-save-key`.
- **A tool returns `{"error": "...access token..."}`** — the saved key
  expired or was deleted on Garmin's side; create a new one and run
  `garmin-mcp-save-key` again.
- **A tool returns `{"error": "No cached Garmin Connect session found..."}`**
  — run `garmin-mcp-auth` (only needed for the 11 unofficial-fallback and
  9 workout tools).
- **`create_workout`/`create_simple_interval_workout` fails with a
  validation error** — the `{"error": "ValueError: ..."}` message says
  exactly what's wrong (e.g. a missing `work_zone_number`, or both
  `work_minutes` and `work_distance_meters` set); fix the arguments and
  retry, no need to touch your Garmin account or re-authenticate.
- **A workout doesn't show up on the watch after `push_workout_to_device`
  or `schedule_workout`** — the device needs to be reachable for sync
  right then (e.g. Garmin Connect Mobile open with Bluetooth connected);
  `schedule_workout` alone just puts it on the calendar for the device's
  next normal sync, it doesn't push immediately.
- **An unofficial-fallback tool's login fails** — check the
  [python-garminconnect issue tracker](https://github.com/cyberjunky/python-garminconnect/issues);
  Garmin's login flow has changed before and will likely change again.
- **Some fields seem missing from official-API activity data** — there
  have been reports (e.g. from Runalyze) that Garmin strips some
  undocumented fields (VO2 max estimate, recovery time, performance
  condition) from API-delivered files. That's a Garmin-side limitation
  of the official API, not a bug here — it's part of why
  `get_max_metrics` still uses the unofficial fallback.
- **claude.ai says it can't connect / the connector keeps failing** —
  check `curl https://<your-domain>/health` returns `ok` first; if that
  fails, check Railway's deploy logs for a missing-env-var error
  (`garmin-mcp-remote-server` refuses to start without
  `GARMIN_PERSONAL_API_KEY`, `GARMIN_MCP_PUBLIC_URL`, and
  `GARMIN_MCP_ACCESS_PASSWORD` all set).
- **The remote server's login page says the link expired** — pending
  logins and issued tokens are kept in memory only, so a Railway
  restart/redeploy clears them; just reconnect the connector in
  claude.ai and log in again.
- **A remote tool call fails but the same tool works locally** — the
  remote server only has the official-API tools; if it's one of the 11
  unofficial-fallback tools (see the table above), that's expected — use
  the local `garmin-mcp-server` for those.

## License

MIT — see [LICENSE](LICENSE).
