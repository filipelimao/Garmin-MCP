"""MCP server exposing the authenticated user's own Garmin Connect data.

Tools are grouped into three areas, matching the data this server was
built to expose:

- Activities & workouts: list/search activities, get details, splits,
  laps, weather, gear, personal records, devices.
- Health & wellness: sleep, heart rate, body battery, stress, steps,
  respiration, SpO2, daily summary.
- Training metrics: training status, training readiness, VO2 max
  (max metrics), HRV, race predictions.

All data returned belongs to whichever Garmin Connect account was logged
in via `garmin-mcp-auth` (see authenticate.py) — there is no concept of
"other users" here. Dates are ISO strings, "YYYY-MM-DD".
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer

from .auth import GarminAuthError, get_client

mcp = MCPServer(
    name="garmin-connect",
    instructions=(
        "Read-only tools for the authenticated user's own Garmin Connect "
        "data: activities/workouts, health & wellness metrics, and "
        "training metrics. Dates are ISO 'YYYY-MM-DD' strings; omit a "
        "date argument to default to today. If a tool returns an "
        "{'error': ...} object about a missing or expired session, tell "
        "the user to run `garmin-mcp-auth` in a terminal and try again."
    ),
)


def _today() -> str:
    return date.today().isoformat()


async def _call(make_call: Callable[[Any], Any]) -> Any:
    """Run a blocking garminconnect call off the event loop.

    Returns the call's result, or an {"error": ...} dict if there's no
    valid cached session or the Garmin API call itself fails (rate limit,
    network issue, invalid id, etc.) — tools never raise, so a single bad
    call can't crash the server process.
    """
    try:
        client = get_client()
    except GarminAuthError as exc:
        return {"error": str(exc)}

    try:
        return await asyncio.to_thread(make_call, client)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the server
        return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# Activities & workouts
# --------------------------------------------------------------------------


@mcp.tool()
async def list_activities(
    start: int = 0, limit: int = 20, activity_type: str | None = None
) -> Any:
    """List the user's most recent Garmin Connect activities.

    Args:
        start: Offset into the activity list, 0 = most recent.
        limit: Maximum number of activities to return.
        activity_type: Optional filter, e.g. "cycling", "running", "swimming", "strength_training".
    """
    return await _call(lambda c: c.get_activities(start, limit, activity_type))


@mcp.tool()
async def list_activities_by_date(
    start_date: str, end_date: str | None = None, activity_type: str | None = None
) -> Any:
    """List activities within a date range (inclusive).

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to today if omitted.
        activity_type: Optional filter, e.g. "cycling", "running".
    """
    return await _call(
        lambda c: c.get_activities_by_date(start_date, end_date or _today(), activity_type)
    )


@mcp.tool()
async def get_last_activity() -> Any:
    """Get the user's single most recent activity."""
    return await _call(lambda c: c.get_last_activity())


@mcp.tool()
async def get_activity(activity_id: str) -> Any:
    """Get summary info for one activity by its Garmin activity id.

    Args:
        activity_id: The Garmin Connect activity id (from list_activities, etc.).
    """
    return await _call(lambda c: c.get_activity(activity_id))


@mcp.tool()
async def get_activity_details(activity_id: str) -> Any:
    """Get full details for one activity: metrics, charts, and GPS polyline data.

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call(lambda c: c.get_activity_details(activity_id))


@mcp.tool()
async def get_activity_splits(activity_id: str) -> Any:
    """Get the per-split (lap/km/mile) breakdown of one activity.

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call(lambda c: c.get_activity_splits(activity_id))


@mcp.tool()
async def get_activity_laps(activity_id: str) -> Any:
    """Get the manually- or auto-lapped segments of one activity.

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call(lambda c: c.get_activity_laps(activity_id))


@mcp.tool()
async def get_activity_weather(activity_id: str) -> Any:
    """Get the recorded weather conditions for one activity, if available.

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call(lambda c: c.get_activity_weather(activity_id))


@mcp.tool()
async def get_activity_gear(activity_id: str) -> Any:
    """Get the gear (shoes, bike, etc.) logged against one activity.

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call(lambda c: c.get_activity_gear(activity_id))


@mcp.tool()
async def get_personal_records() -> Any:
    """Get the user's current personal records (fastest 5k, longest ride, etc.)."""
    return await _call(lambda c: c.get_personal_record())


@mcp.tool()
async def list_devices() -> Any:
    """List the Garmin devices registered to the user's account."""
    return await _call(lambda c: c.get_devices())


# --------------------------------------------------------------------------
# Health & wellness
# --------------------------------------------------------------------------


@mcp.tool()
async def get_daily_summary(day: str | None = None) -> Any:
    """Get the overall daily stats summary for one day (steps, calories, HR, etc.).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today if omitted.
    """
    return await _call(lambda c: c.get_user_summary(day or _today()))


@mcp.tool()
async def get_sleep(day: str | None = None) -> Any:
    """Get sleep data (stages, score, duration) for the night ending on this day.

    Args:
        day: ISO date "YYYY-MM-DD" the sleep session is logged under. Defaults to today.
    """
    return await _call(lambda c: c.get_sleep_data(day or _today()))


@mcp.tool()
async def get_heart_rate(day: str | None = None) -> Any:
    """Get all-day heart rate data (resting, min, max, and the intraday time series) for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_heart_rates(day or _today()))


@mcp.tool()
async def get_resting_heart_rate(day: str | None = None) -> Any:
    """Get just the resting heart rate value for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_rhr_day(day or _today()))


@mcp.tool()
async def get_body_battery(start_date: str, end_date: str | None = None) -> Any:
    """Get Body Battery (energy reserve) levels over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date if omitted.
    """
    return await _call(lambda c: c.get_body_battery(start_date, end_date))


@mcp.tool()
async def get_stress(day: str | None = None) -> Any:
    """Get all-day stress level data for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_stress_data(day or _today()))


@mcp.tool()
async def get_steps(day: str | None = None) -> Any:
    """Get the intraday step-count time series for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_steps_data(day or _today()))


@mcp.tool()
async def get_respiration(day: str | None = None) -> Any:
    """Get all-day respiration (breaths per minute) data for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_respiration_data(day or _today()))


@mcp.tool()
async def get_spo2(day: str | None = None) -> Any:
    """Get all-day blood oxygen saturation (SpO2) data for one day, if the device supports it.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_spo2_data(day or _today()))


# --------------------------------------------------------------------------
# Training metrics
# --------------------------------------------------------------------------


@mcp.tool()
async def get_training_status(day: str | None = None) -> Any:
    """Get Garmin's training status assessment (e.g. productive, overreaching) for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_training_status(day or _today()))


@mcp.tool()
async def get_training_readiness(day: str | None = None) -> Any:
    """Get Garmin's training readiness score and contributing factors for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_training_readiness(day or _today()))


@mcp.tool()
async def get_max_metrics(day: str | None = None) -> Any:
    """Get max metrics (VO2 max, fitness age) as of one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_max_metrics(day or _today()))


@mcp.tool()
async def get_hrv(day: str | None = None) -> Any:
    """Get heart rate variability (HRV) status and overnight data for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call(lambda c: c.get_hrv_data(day or _today()))


@mcp.tool()
async def get_race_predictions() -> Any:
    """Get Garmin's predicted race times (5k/10k/half/full marathon) based on current fitness."""
    return await _call(lambda c: c.get_race_predictions())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
