"""MCP server exposing the authenticated user's own Garmin Connect data.

This is a **hybrid** server, because Garmin's official Personal API
(launched October 2026) doesn't yet cover everything the unofficial
`garminconnect` library does:

- Most tools call Garmin's own published client,
  `garmin-connect-personal-api-python`
  (https://github.com/garmin/connect-personal-api-python), authenticated
  with a Personal API key you generate yourself — no password, no MFA,
  fully within Garmin's terms of service. See auth_official.py.
- A smaller set of tools — training status/readiness, VO2 max, race
  predictions, personal records, devices, activity splits/laps/weather/
  gear, and respiration — aren't exposed by the official API yet, so
  those fall back to the unofficial `garminconnect`/`garth` library,
  which logs in with your Garmin email/password once via
  `garmin-mcp-auth`. Every such tool's docstring starts with
  "(unofficial API)" so it's always clear which backend is in play. See
  auth_unofficial.py for the caveats that come with it.

You only need to set up the backend(s) a tool you actually want to use
requires — if you never call an unofficial-API tool, you never need to
run `garmin-mcp-auth` at all.

Dates are ISO strings, "YYYY-MM-DD"; omitting one defaults to today.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Callable

from garmin_personal_api import HttpError
from mcp.server.mcpserver import MCPServer

from . import auth_official, auth_unofficial

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


def _today() -> str:
    return date.today().isoformat()


async def _call_official(make_call: Callable[[Any], Any], *, _retried: bool = False) -> Any:
    """Run a blocking official-API call off the event loop.

    Transparently refreshes the access token and retries once on a 401
    (the official client has no built-in retry for an expired/revoked
    token). Returns an {"error": ...} dict instead of raising, so one
    bad call can't crash the server.
    """
    try:
        client = auth_official.get_client()
    except auth_official.OfficialAuthError as exc:
        return {"error": str(exc)}

    try:
        return await asyncio.to_thread(make_call, client)
    except HttpError as exc:
        if exc.status_code == 401 and not _retried:
            auth_official.reset_client()
            return await _call_official(make_call, _retried=True)
        return {"error": f"HTTP {exc.status_code} from Garmin Personal API: {exc}"}
    except Exception as exc:  # noqa: BLE001 - report, don't crash the server
        return {"error": f"{type(exc).__name__}: {exc}"}


async def _call_unofficial(make_call: Callable[[Any], Any]) -> Any:
    """Run a blocking unofficial-library (garminconnect) call off the event loop."""
    try:
        client = auth_unofficial.get_client()
    except auth_unofficial.GarminAuthError as exc:
        return {"error": str(exc)}

    try:
        return await asyncio.to_thread(make_call, client)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the server
        return {"error": f"{type(exc).__name__}: {exc}"}


# ==========================================================================
# Official Garmin Personal API
# ==========================================================================

# ---- Activities & workouts ----------------------------------------------


@mcp.tool()
async def list_activities(
    start: int = 0,
    limit: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    activity_type: str | None = None,
) -> Any:
    """List the user's Garmin Connect activities, most recent first.

    Args:
        start: Offset into the activity list, 0 = most recent.
        limit: Maximum number of activities to return.
        start_date: Optional ISO date "YYYY-MM-DD" to filter from.
        end_date: Optional ISO date "YYYY-MM-DD" to filter to.
        activity_type: Optional filter, e.g. "cycling", "running", "swimming".
    """
    return await _call_official(
        lambda c: c.search_activities(
            start=start, limit=limit, start_date=start_date, end_date=end_date, activity_type=activity_type
        )
    )


@mcp.tool()
async def get_activity(activity_id: str) -> Any:
    """Get summary info for one activity by its Garmin activity id.

    Args:
        activity_id: The Garmin Connect activity id (from list_activities).
    """
    return await _call_official(lambda c: c.get_activity(activity_id))


@mcp.tool()
async def get_activity_workouts(activity_id: str) -> Any:
    """Get the structured workout (if any) associated with one activity.

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_official(lambda c: c.get_activity_workouts(activity_id))


@mcp.tool()
async def get_wellness_activity_summaries(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get auto-detected wellness activities (e.g. untracked walks) over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(
        lambda c: c.get_wellness_activity_summaries(start_date, end_date or start_date)
    )


# ---- Health & wellness ----------------------------------------------------


@mcp.tool()
async def get_daily_summary(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get the overall daily stats summary (steps, calories, HR, etc.) over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_summaries(start_date, end_date or start_date))


@mcp.tool()
async def get_sleep(day: str | None = None) -> Any:
    """Get sleep data (stages, score, duration) for the night ending on this day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_sleep(day or _today()))


@mcp.tool()
async def get_sleep_summaries(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get sleep summaries (duration/score trend) over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_sleep_summaries(start_date, end_date or start_date))


@mcp.tool()
async def get_steps(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get daily step counts over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_steps(start_date, end_date or start_date))


@mcp.tool()
async def get_heart_rate(day: str | None = None) -> Any:
    """Get all-day heart rate data (including resting HR) for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_heart_rate(day or _today()))


@mcp.tool()
async def get_heart_rate_zones() -> Any:
    """Get the user's configured heart rate zones."""
    return await _call_official(lambda c: c.get_heart_rate_zones())


@mcp.tool()
async def get_body_battery(
    start_date: str | None = None, end_date: str | None = None, chunks: int | None = None
) -> Any:
    """Get Body Battery (energy reserve) levels over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
        chunks: Optional number of sub-intervals to split each day into.
    """
    start_date = start_date or _today()
    return await _call_official(
        lambda c: c.get_daily_body_battery(start_date, end_date or start_date, chunks=chunks)
    )


@mcp.tool()
async def get_stress(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get all-day stress level data over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_stress(start_date, end_date or start_date))


@mcp.tool()
async def get_hrv(day: str | None = None) -> Any:
    """Get heart rate variability (HRV) data for one night.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_hrv(day or _today()))


@mcp.tool()
async def get_hrv_summaries(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get HRV status/trend summaries over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_hrv_summaries(start_date, end_date or start_date))


@mcp.tool()
async def get_spo2(day: str | None = None) -> Any:
    """Get all-day blood oxygen saturation (SpO2) data for one day, if the device supports it.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_spo2(day or _today()))


@mcp.tool()
async def get_fitness_age(day: str | None = None) -> Any:
    """Get Garmin's estimated fitness age as of one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_fitness_age(day or _today()))


@mcp.tool()
async def get_hydration(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get logged hydration data over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_hydration(start_date, end_date or start_date))


@mcp.tool()
async def get_weight_measurements(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get logged weight/body composition measurements over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_weight_measurements(start_date, end_date or start_date))


@mcp.tool()
async def get_weight_goals(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get the user's effective weight goals over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_effective_weight_goals(start_date, end_date or start_date))


@mcp.tool()
async def get_food_logs(day: str | None = None) -> Any:
    """Get logged food/nutrition entries for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_food_logs(day or _today()))


@mcp.tool()
async def get_profile_settings() -> Any:
    """Get the user's Garmin Connect profile settings (units, measurement preferences, etc.)."""
    return await _call_official(lambda c: c.get_user_profile_settings())


# ---- Women's health ---------------------------------------------------


@mcp.tool()
async def get_menstrual_cycle_summary(day: str | None = None) -> Any:
    """Get the menstrual cycle summary covering the given day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_menstrual_cycle_summary(day or _today()))


@mcp.tool()
async def get_menstrual_cycle_day_view(day: str | None = None) -> Any:
    """Get the menstrual cycle day-view (phase, predictions) for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    today = _today()
    return await _call_official(lambda c: c.get_menstrual_cycle_day_view(day or today, today))


# ==========================================================================
# Unofficial fallback (garminconnect/garth) — data Garmin's official
# Personal API doesn't expose yet. Every tool below is clearly labeled.
# ==========================================================================


@mcp.tool()
async def get_respiration(day: str | None = None) -> Any:
    """(unofficial API) Get all-day respiration (breaths per minute) data for one day.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_unofficial(lambda c: c.get_respiration_data(day or _today()))


@mcp.tool()
async def get_training_status(day: str | None = None) -> Any:
    """(unofficial API) Get Garmin's training status assessment (e.g. productive, overreaching) for one day.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_unofficial(lambda c: c.get_training_status(day or _today()))


@mcp.tool()
async def get_training_readiness(day: str | None = None) -> Any:
    """(unofficial API) Get Garmin's training readiness score and contributing factors for one day.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_unofficial(lambda c: c.get_training_readiness(day or _today()))


@mcp.tool()
async def get_max_metrics(day: str | None = None) -> Any:
    """(unofficial API) Get max metrics (VO2 max, fitness age) as of one day.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_unofficial(lambda c: c.get_max_metrics(day or _today()))


@mcp.tool()
async def get_race_predictions() -> Any:
    """(unofficial API) Get Garmin's predicted race times (5k/10k/half/full marathon).

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).
    """
    return await _call_unofficial(lambda c: c.get_race_predictions())


@mcp.tool()
async def get_personal_records() -> Any:
    """(unofficial API) Get the user's current personal records (fastest 5k, longest ride, etc.).

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).
    """
    return await _call_unofficial(lambda c: c.get_personal_record())


@mcp.tool()
async def list_devices() -> Any:
    """(unofficial API) List the Garmin devices registered to the user's account.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).
    """
    return await _call_unofficial(lambda c: c.get_devices())


@mcp.tool()
async def get_activity_splits(activity_id: str) -> Any:
    """(unofficial API) Get the per-split (lap/km/mile) breakdown of one activity.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_unofficial(lambda c: c.get_activity_splits(activity_id))


@mcp.tool()
async def get_activity_laps(activity_id: str) -> Any:
    """(unofficial API) Get the manually- or auto-lapped segments of one activity.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_unofficial(lambda c: c.get_activity_laps(activity_id))


@mcp.tool()
async def get_activity_weather(activity_id: str) -> Any:
    """(unofficial API) Get the recorded weather conditions for one activity, if available.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_unofficial(lambda c: c.get_activity_weather(activity_id))


@mcp.tool()
async def get_activity_gear(activity_id: str) -> Any:
    """(unofficial API) Get the gear (shoes, bike, etc.) logged against one activity.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_unofficial(lambda c: c.get_activity_gear(activity_id))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
