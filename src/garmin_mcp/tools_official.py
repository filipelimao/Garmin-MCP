"""Tool implementations backed by Garmin's official Personal API.

These are plain, undecorated async functions — not tied to any one
``MCPServer`` instance — so both the local hybrid server (``server.py``,
stdio, all 34 tools) and the remote server (``remote_server.py``, HTTP,
official-only) can register the exact same 23 functions via
``mcp.add_tool(fn)`` instead of maintaining two copies.

Dates are ISO strings, "YYYY-MM-DD"; omitting one defaults to today.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Callable

from garmin_personal_api import HttpError

from . import auth_official


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


# ---- Activities & workouts ----------------------------------------------


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


async def get_activity(activity_id: str) -> Any:
    """Get summary info for one activity by its Garmin activity id.

    Args:
        activity_id: The Garmin Connect activity id (from list_activities).
    """
    return await _call_official(lambda c: c.get_activity(activity_id))


async def get_activity_workouts(activity_id: str) -> Any:
    """Get the structured workout (if any) associated with one activity.

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_official(lambda c: c.get_activity_workouts(activity_id))


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


async def get_daily_summary(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get the overall daily stats summary (steps, calories, HR, etc.) over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_summaries(start_date, end_date or start_date))


async def get_sleep(day: str | None = None) -> Any:
    """Get sleep data (stages, score, duration) for the night ending on this day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_sleep(day or _today()))


async def get_sleep_summaries(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get sleep summaries (duration/score trend) over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_sleep_summaries(start_date, end_date or start_date))


async def get_steps(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get daily step counts over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_steps(start_date, end_date or start_date))


async def get_heart_rate(day: str | None = None) -> Any:
    """Get all-day heart rate data (including resting HR) for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_heart_rate(day or _today()))


async def get_heart_rate_zones() -> Any:
    """Get the user's configured heart rate zones."""
    return await _call_official(lambda c: c.get_heart_rate_zones())


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


async def get_stress(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get all-day stress level data over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_stress(start_date, end_date or start_date))


async def get_hrv(day: str | None = None) -> Any:
    """Get heart rate variability (HRV) data for one night.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_hrv(day or _today()))


async def get_hrv_summaries(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get HRV status/trend summaries over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_hrv_summaries(start_date, end_date or start_date))


async def get_spo2(day: str | None = None) -> Any:
    """Get all-day blood oxygen saturation (SpO2) data for one day, if the device supports it.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_spo2(day or _today()))


async def get_fitness_age(day: str | None = None) -> Any:
    """Get Garmin's estimated fitness age as of one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_fitness_age(day or _today()))


async def get_hydration(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get logged hydration data over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_daily_hydration(start_date, end_date or start_date))


async def get_weight_measurements(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get logged weight/body composition measurements over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_weight_measurements(start_date, end_date or start_date))


async def get_weight_goals(start_date: str | None = None, end_date: str | None = None) -> Any:
    """Get the user's effective weight goals over a date range.

    Args:
        start_date: ISO date "YYYY-MM-DD" to start from. Defaults to today.
        end_date: ISO date "YYYY-MM-DD" to end at. Defaults to start_date.
    """
    start_date = start_date or _today()
    return await _call_official(lambda c: c.get_effective_weight_goals(start_date, end_date or start_date))


async def get_food_logs(day: str | None = None) -> Any:
    """Get logged food/nutrition entries for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_daily_food_logs(day or _today()))


async def get_profile_settings() -> Any:
    """Get the user's Garmin Connect profile settings (units, measurement preferences, etc.)."""
    return await _call_official(lambda c: c.get_user_profile_settings())


# ---- Women's health ---------------------------------------------------


async def get_menstrual_cycle_summary(day: str | None = None) -> Any:
    """Get the menstrual cycle summary covering the given day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_official(lambda c: c.get_menstrual_cycle_summary(day or _today()))


async def get_menstrual_cycle_day_view(day: str | None = None) -> Any:
    """Get the menstrual cycle day-view (phase, predictions) for one day.

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    today = _today()
    return await _call_official(lambda c: c.get_menstrual_cycle_day_view(day or today, today))


# All 23 official-API tools, in a stable order. Both server.py (local,
# hybrid) and remote_server.py (remote, official-only) register tools by
# iterating this list — add a new official tool here once and it shows up
# in both places.
OFFICIAL_TOOLS: list[Callable[..., Any]] = [
    list_activities,
    get_activity,
    get_activity_workouts,
    get_wellness_activity_summaries,
    get_daily_summary,
    get_sleep,
    get_sleep_summaries,
    get_steps,
    get_heart_rate,
    get_heart_rate_zones,
    get_body_battery,
    get_stress,
    get_hrv,
    get_hrv_summaries,
    get_spo2,
    get_fitness_age,
    get_hydration,
    get_weight_measurements,
    get_weight_goals,
    get_food_logs,
    get_profile_settings,
    get_menstrual_cycle_summary,
    get_menstrual_cycle_day_view,
]
