"""Tool implementations for creating and pushing structured workouts.

Separate from `tools_unofficial.py` (which is read-only) because these
tools *write* to the user's Garmin Connect account: they create, schedule,
push, and delete workouts. They use the same unofficial `garminconnect`
library and the same session-source pattern — a factory,
`build_workout_tools(get_client)`, bound to either the local, file-cached
session (`auth_unofficial.get_client`) or the remote, in-memory session
established at `/admin/garmin-login` (`remote_auth_unofficial.get_client`).

Garmin's official Personal API doesn't cover workout creation at all yet
(as of the October 2026 revamp, that's part of the separate, free
"Startup" developer tier's Courses & Training APIs — a different
application entirely), so every tool here is unofficial-API-only, same as
`tools_unofficial.py`. Every docstring starts with "(unofficial API)".

Two ways to build a workout:

- `create_simple_interval_workout` covers the common case — warmup, N
  repeats of a work/recovery interval, cooldown — for running or cycling,
  with an optional heart-rate zone, power zone, or pace target on the work
  interval. No knowledge of Garmin's internal workout JSON required.
- `create_workout` takes a fully custom workout as raw JSON, for anything
  `create_simple_interval_workout` doesn't cover (multi-sport, swimming,
  strength sets, custom step sequences, distance-based repeats, secondary
  targets, etc.). Use `get_workout_types` first to see the valid step/
  target/sport/equipment type ids referenced in that JSON.

Garmin has no walking or hiking workout type — only running, cycling,
swimming, strength training, cardio, yoga, pilates, HIIT, multi-sport, and
mobility can be uploaded.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


def _build_simple_interval_workout(
    *,
    sport: str,
    name: str,
    repeats: int,
    work_minutes: float | None,
    work_distance_meters: float | None,
    work_target: str | None,
    work_zone_number: int | None,
    work_pace_min_per_km: float | None,
    work_pace_tolerance_pct: float,
    recovery_minutes: float,
    warmup_minutes: float,
    cooldown_minutes: float,
    description: str | None,
) -> dict[str, Any]:
    """Build the Garmin workout JSON for a warmup/interval-repeat/cooldown session.

    Runs synchronously (no network call) so it can raise plain `ValueError`s
    for bad input; the caller runs it inside the same `asyncio.to_thread`
    call as the upload, so those errors surface as the tool's normal
    `{"error": ...}` response like everything else here.
    """
    from garminconnect.workout import (
        CyclingWorkout,
        HeartRateZoneTarget,
        PaceTarget,
        PowerZoneTarget,
        RunningWorkout,
        WorkoutSegment,
        create_cooldown_step,
        create_distance_interval_step,
        create_interval_step,
        create_recovery_step,
        create_repeat_group,
        create_targeted_distance_interval_step,
        create_targeted_interval_step,
        create_warmup_step,
        pace_to_mps,
    )

    sport = sport.lower().strip()
    if sport not in ("running", "cycling"):
        raise ValueError(
            f"sport must be 'running' or 'cycling', got {sport!r} — Garmin has no "
            "uploadable walking/hiking workout type; use create_workout for other "
            "sports (swimming, strength, etc.)"
        )
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if not work_minutes and not work_distance_meters:
        raise ValueError("specify either work_minutes or work_distance_meters for the work interval")
    if work_minutes and work_distance_meters:
        raise ValueError("specify only one of work_minutes or work_distance_meters, not both")

    target: Any = None
    if work_target and work_target != "none":
        if work_target == "heart_rate_zone":
            if not work_zone_number:
                raise ValueError("work_zone_number is required when work_target='heart_rate_zone'")
            target = HeartRateZoneTarget(zone_number=work_zone_number)
        elif work_target == "power_zone":
            if not work_zone_number:
                raise ValueError("work_zone_number is required when work_target='power_zone'")
            target = PowerZoneTarget(zone_number=work_zone_number)
        elif work_target == "pace":
            if not work_pace_min_per_km:
                raise ValueError("work_pace_min_per_km is required when work_target='pace'")
            minutes = int(work_pace_min_per_km)
            seconds = round((work_pace_min_per_km - minutes) * 60)
            center_mps = pace_to_mps(minutes, seconds, "km")
            tolerance = max(work_pace_tolerance_pct, 0.0) / 100.0
            target = PaceTarget(
                lower_limit=center_mps * (1 - tolerance),
                upper_limit=center_mps * (1 + tolerance),
            )
        else:
            raise ValueError(
                f"work_target must be 'none', 'heart_rate_zone', 'power_zone', or 'pace', got {work_target!r}"
            )

    inner_steps: list[Any] = []
    if work_distance_meters:
        work_step = (
            create_targeted_distance_interval_step(work_distance_meters, 1, target=target)
            if target is not None
            else create_distance_interval_step(work_distance_meters, 1)
        )
    else:
        work_step = (
            create_targeted_interval_step((work_minutes or 0) * 60, 1, target=target)
            if target is not None
            else create_interval_step((work_minutes or 0) * 60, 1)
        )
    inner_steps.append(work_step)

    has_recovery = bool(recovery_minutes and recovery_minutes > 0)
    if has_recovery:
        inner_steps.append(create_recovery_step(recovery_minutes * 60, 2))

    outer_steps: list[Any] = []
    order = 1
    if warmup_minutes and warmup_minutes > 0:
        outer_steps.append(create_warmup_step(warmup_minutes * 60, step_order=order))
        order += 1

    outer_steps.append(create_repeat_group(repeats, inner_steps, step_order=order))
    order += 1

    if cooldown_minutes and cooldown_minutes > 0:
        outer_steps.append(create_cooldown_step(cooldown_minutes * 60, step_order=order))
        order += 1

    sport_type = (
        {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
        if sport == "running"
        else {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2}
    )
    segment = WorkoutSegment(segmentOrder=1, sportType=sport_type, workoutSteps=outer_steps)

    # Best-effort duration estimate for the required field; Garmin
    # recalculates actual duration from the steps on its own side anyway.
    # Distance-based intervals contribute 0 here since pace is unknown.
    estimated_seconds = int(
        (warmup_minutes or 0) * 60
        + repeats * ((work_minutes or 0) * 60 + (recovery_minutes if has_recovery else 0) * 60)
        + (cooldown_minutes or 0) * 60
    )

    workout_cls = RunningWorkout if sport == "running" else CyclingWorkout
    workout = workout_cls(
        workoutName=name,
        estimatedDurationInSecs=max(estimated_seconds, 1),
        workoutSegments=[segment],
        description=description,
    )
    return workout.to_dict()


def build_workout_tools(get_client: Callable[[], Any]) -> list[Callable[..., Any]]:
    """Return the workout-creation/scheduling/push tool callables, bound to `get_client`.

    `get_client()` must return a logged-in `garminconnect.Garmin` instance,
    or raise so its exception's `str()` becomes the `{"error": ...}` message
    returned to the caller — same contract as `tools_unofficial.build_unofficial_tools`.
    """

    async def _call_unofficial(make_call: Callable[[Any], Any]) -> Any:
        try:
            client = get_client()
        except Exception as exc:  # noqa: BLE001 - no session yet; report, don't crash
            return {"error": str(exc)}

        try:
            return await asyncio.to_thread(make_call, client)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the server
            return {"error": f"{type(exc).__name__}: {exc}"}

    async def get_workout_types() -> Any:
        """(unofficial API) List Garmin's valid workout step/target/sport/equipment type ids.

        Use this before calling create_workout with raw JSON, to find the
        correct ids/keys for stepType, targetType, sportType, equipmentType,
        etc. Not needed for create_simple_interval_workout.
        """
        return await _call_unofficial(lambda c: c.get_workout_types())

    async def create_simple_interval_workout(
        sport: str,
        name: str,
        repeats: int,
        work_minutes: float | None = None,
        work_distance_meters: float | None = None,
        work_target: str | None = None,
        work_zone_number: int | None = None,
        work_pace_min_per_km: float | None = None,
        work_pace_tolerance_pct: float = 3.0,
        recovery_minutes: float = 0,
        warmup_minutes: float = 0,
        cooldown_minutes: float = 0,
        description: str | None = None,
    ) -> Any:
        """(unofficial API) Create and upload a warmup/interval-repeat/cooldown structured workout.

        Covers the common case — e.g. "6x800m at 10K pace with 2 min jog
        recovery, 10 min warmup, 5 min cooldown" — without needing to know
        Garmin's internal workout JSON schema. For anything this doesn't
        cover (swimming, strength sets, multi-sport, distance targets other
        than the work interval itself), use create_workout with raw JSON
        instead.

        Args:
            sport: "running" or "cycling" (Garmin has no uploadable walking
                or hiking workout type).
            name: Workout name shown in Garmin Connect.
            repeats: Number of work/recovery repeats.
            work_minutes: Work-interval duration in minutes. Mutually
                exclusive with work_distance_meters — set exactly one.
            work_distance_meters: Work-interval distance in meters instead
                of a fixed duration.
            work_target: Optional target for the work interval: "none"
                (default), "heart_rate_zone", "power_zone", or "pace".
            work_zone_number: Zone number (1-5, as configured in the user's
                Garmin profile) — required when work_target is
                "heart_rate_zone" or "power_zone".
            work_pace_min_per_km: Target pace in decimal minutes per km
                (e.g. 4.5 = 4:30/km) — required when work_target is "pace".
                Garmin gets a +/- range around this, not an exact value.
            work_pace_tolerance_pct: Width of that pace range as a percent
                of the target pace in each direction. Default 3.0 (+/-3%).
            recovery_minutes: Recovery duration in minutes between repeats.
                0 (default) omits the recovery step entirely.
            warmup_minutes: Warmup duration in minutes before the repeats.
                0 (default) omits the warmup step.
            cooldown_minutes: Cooldown duration in minutes after the
                repeats. 0 (default) omits the cooldown step.
            description: Optional free-text note shown with the workout.
        """

        def _make(client: Any) -> Any:
            workout_json = _build_simple_interval_workout(
                sport=sport,
                name=name,
                repeats=repeats,
                work_minutes=work_minutes,
                work_distance_meters=work_distance_meters,
                work_target=work_target,
                work_zone_number=work_zone_number,
                work_pace_min_per_km=work_pace_min_per_km,
                work_pace_tolerance_pct=work_pace_tolerance_pct,
                recovery_minutes=recovery_minutes,
                warmup_minutes=warmup_minutes,
                cooldown_minutes=cooldown_minutes,
                description=description,
            )
            return client.upload_workout(workout_json)

        return await _call_unofficial(_make)

    async def create_workout(workout_json: dict[str, Any] | list[Any] | str) -> Any:
        """(unofficial API) Upload a fully custom structured workout from raw Garmin workout JSON.

        For the common warmup/interval-repeat/cooldown case, use
        create_simple_interval_workout instead — it builds this JSON for
        you. Use get_workout_types first to find the correct step/target/
        sport/equipment type ids for anything you build here by hand (e.g.
        swimming, strength sets, multi-sport, secondary targets, distance-
        based repeats).

        Args:
            workout_json: The workout as a dict (or JSON string) matching
                Garmin's internal workout schema — the same shape returned
                by get_workout / list_workouts.
        """
        return await _call_unofficial(lambda c: c.upload_workout(workout_json))

    async def list_workouts(limit: int = 100) -> Any:
        """(unofficial API) List workouts saved in the user's Garmin Connect workout library.

        Args:
            limit: Maximum number of workouts to return. Default 100.
        """
        return await _call_unofficial(lambda c: c.get_workouts(limit=limit))

    async def get_workout(workout_id: str) -> Any:
        """(unofficial API) Get the full structured definition of one saved workout.

        Args:
            workout_id: Workout id, e.g. from list_workouts or
                create_simple_interval_workout's/create_workout's result.
        """
        return await _call_unofficial(lambda c: c.get_workout_by_id(workout_id))

    async def delete_workout(workout_id: str) -> Any:
        """(unofficial API) Permanently delete a saved workout from the user's Garmin Connect library.

        Args:
            workout_id: Workout id, e.g. from list_workouts.
        """
        return await _call_unofficial(lambda c: c.delete_workout(workout_id))

    async def schedule_workout(workout_id: str, date: str) -> Any:
        """(unofficial API) Schedule a saved workout on a specific calendar date.

        A scheduled workout appears on the Garmin Connect calendar and is
        picked up by the user's device on its next normal sync — or use
        push_workout_to_device / send_scheduled_workouts_to_devices to push
        it immediately instead of waiting for that sync.

        Args:
            workout_id: Workout id, e.g. from list_workouts or
                create_simple_interval_workout's/create_workout's result.
            date: ISO date "YYYY-MM-DD" to schedule the workout on.
        """
        return await _call_unofficial(lambda c: c.schedule_workout(workout_id, date))

    async def push_workout_to_device(
        workout_id: str | None = None, device_id: str | None = None
    ) -> Any:
        """(unofficial API) Push one workout directly to a connected Garmin device right now.

        Requires the device to be currently reachable for sync (e.g. open
        Garmin Connect Mobile with Bluetooth connected to the watch).

        Args:
            workout_id: Workout id to push. Defaults to the most recently
                uploaded workout if omitted.
            device_id: Device id to push to — see list_devices (in
                tools_unofficial). Defaults to the user's last-used device
                if omitted.
        """
        return await _call_unofficial(lambda c: c.push_workout_to_device(workout_id, device_id))

    async def send_scheduled_workouts_to_devices() -> Any:
        """(unofficial API) Push every workout currently scheduled on the calendar to the user's device(s) now.

        Requires the device(s) to be currently reachable for sync (e.g.
        open Garmin Connect Mobile with Bluetooth connected).
        """
        return await _call_unofficial(lambda c: c.send_scheduled_workouts_to_devices())

    return [
        get_workout_types,
        create_simple_interval_workout,
        create_workout,
        list_workouts,
        get_workout,
        delete_workout,
        schedule_workout,
        push_workout_to_device,
        send_scheduled_workouts_to_devices,
    ]
