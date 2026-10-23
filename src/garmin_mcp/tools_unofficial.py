"""Tool implementations backed by the unofficial garminconnect/garth library.

Only the local hybrid server (``server.py``) registers these — the remote
server (``remote_server.py``) is official-API-only, so your Garmin
password never has to exist anywhere but your own machine. Every one of
these functions' docstrings starts with "(unofficial API)" so it's always
clear which backend answered, including in `list_tools` output.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Callable

from . import auth_unofficial


def _today() -> str:
    return date.today().isoformat()


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


async def get_respiration(day: str | None = None) -> Any:
    """(unofficial API) Get all-day respiration (breaths per minute) data for one day.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_unofficial(lambda c: c.get_respiration_data(day or _today()))


async def get_training_status(day: str | None = None) -> Any:
    """(unofficial API) Get Garmin's training status assessment (e.g. productive, overreaching) for one day.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_unofficial(lambda c: c.get_training_status(day or _today()))


async def get_training_readiness(day: str | None = None) -> Any:
    """(unofficial API) Get Garmin's training readiness score and contributing factors for one day.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_unofficial(lambda c: c.get_training_readiness(day or _today()))


async def get_max_metrics(day: str | None = None) -> Any:
    """(unofficial API) Get max metrics (VO2 max, fitness age) as of one day.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        day: ISO date "YYYY-MM-DD". Defaults to today.
    """
    return await _call_unofficial(lambda c: c.get_max_metrics(day or _today()))


async def get_race_predictions() -> Any:
    """(unofficial API) Get Garmin's predicted race times (5k/10k/half/full marathon).

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).
    """
    return await _call_unofficial(lambda c: c.get_race_predictions())


async def get_personal_records() -> Any:
    """(unofficial API) Get the user's current personal records (fastest 5k, longest ride, etc.).

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).
    """
    return await _call_unofficial(lambda c: c.get_personal_record())


async def list_devices() -> Any:
    """(unofficial API) List the Garmin devices registered to the user's account.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).
    """
    return await _call_unofficial(lambda c: c.get_devices())


async def get_activity_splits(activity_id: str) -> Any:
    """(unofficial API) Get the per-split (lap/km/mile) breakdown of one activity.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_unofficial(lambda c: c.get_activity_splits(activity_id))


async def get_activity_laps(activity_id: str) -> Any:
    """(unofficial API) Get the manually- or auto-lapped segments of one activity.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_unofficial(lambda c: c.get_activity_laps(activity_id))


async def get_activity_weather(activity_id: str) -> Any:
    """(unofficial API) Get the recorded weather conditions for one activity, if available.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_unofficial(lambda c: c.get_activity_weather(activity_id))


async def get_activity_gear(activity_id: str) -> Any:
    """(unofficial API) Get the gear (shoes, bike, etc.) logged against one activity.

    Not yet available via Garmin's official Personal API, so this uses
    the unofficial garminconnect library (needs `garmin-mcp-auth`).

    Args:
        activity_id: The Garmin Connect activity id.
    """
    return await _call_unofficial(lambda c: c.get_activity_gear(activity_id))


# All 11 unofficial-fallback tools. Only server.py (local, hybrid)
# registers these.
UNOFFICIAL_TOOLS: list[Callable[..., Any]] = [
    get_respiration,
    get_training_status,
    get_training_readiness,
    get_max_metrics,
    get_race_predictions,
    get_personal_records,
    list_devices,
    get_activity_splits,
    get_activity_laps,
    get_activity_weather,
    get_activity_gear,
]
