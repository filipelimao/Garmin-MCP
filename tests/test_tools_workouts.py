"""Offline unit tests for the pure workout-JSON builder in tools_workouts.py.

`_build_simple_interval_workout` never touches the network — it only
assembles a dict using the real `garminconnect.workout` typed builders —
so these tests check its branching logic directly, separately from the
MCP-tool-dispatch tests in test_server.py / test_remote_server.py.

Run with: pytest
"""

from __future__ import annotations

import pytest

from garmin_mcp.tools_workouts import _build_simple_interval_workout


def _base(**overrides):
    base = dict(
        sport="running",
        name="test workout",
        repeats=3,
        work_minutes=None,
        work_distance_meters=None,
        work_target=None,
        work_zone_number=None,
        work_pace_min_per_km=None,
        work_pace_tolerance_pct=3.0,
        recovery_minutes=0,
        warmup_minutes=0,
        cooldown_minutes=0,
        description=None,
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Validation errors
# --------------------------------------------------------------------------


def test_requires_work_minutes_or_distance():
    with pytest.raises(ValueError, match="work_minutes or work_distance_meters"):
        _build_simple_interval_workout(**_base())


def test_rejects_both_work_minutes_and_distance():
    with pytest.raises(ValueError, match="only one of"):
        _build_simple_interval_workout(**_base(work_minutes=5, work_distance_meters=1000))


def test_rejects_unsupported_sport():
    with pytest.raises(ValueError, match="running.*or.*cycling"):
        _build_simple_interval_workout(**_base(sport="walking", work_minutes=5))


def test_rejects_zero_repeats():
    with pytest.raises(ValueError, match="repeats must be at least 1"):
        _build_simple_interval_workout(**_base(repeats=0, work_minutes=5))


def test_heart_rate_zone_requires_zone_number():
    with pytest.raises(ValueError, match="work_zone_number is required"):
        _build_simple_interval_workout(**_base(work_minutes=5, work_target="heart_rate_zone"))


def test_power_zone_requires_zone_number():
    with pytest.raises(ValueError, match="work_zone_number is required"):
        _build_simple_interval_workout(**_base(work_minutes=5, work_target="power_zone"))


def test_pace_requires_pace_value():
    with pytest.raises(ValueError, match="work_pace_min_per_km is required"):
        _build_simple_interval_workout(**_base(work_minutes=5, work_target="pace"))


def test_rejects_unknown_target_type():
    with pytest.raises(ValueError, match="work_target must be"):
        _build_simple_interval_workout(**_base(work_minutes=5, work_target="bogus"))


# --------------------------------------------------------------------------
# Shape of the built workout
# --------------------------------------------------------------------------


def test_duration_based_no_target_shape():
    wj = _build_simple_interval_workout(
        **_base(work_minutes=3, recovery_minutes=2, warmup_minutes=10, cooldown_minutes=5)
    )
    assert wj["workoutName"] == "test workout"
    assert wj["sportType"] == {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
    segment = wj["workoutSegments"][0]
    steps = segment["workoutSteps"]
    # warmup, repeat-group, cooldown
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["warmup", "repeat", "cooldown"]
    repeat_group = steps[1]
    assert repeat_group["numberOfIterations"] == 3
    inner_keys = [s["stepType"]["stepTypeKey"] for s in repeat_group["workoutSteps"]]
    assert inner_keys == ["interval", "recovery"]
    assert repeat_group["workoutSteps"][0]["endConditionValue"] == 180.0  # 3 min
    assert repeat_group["workoutSteps"][1]["endConditionValue"] == 120.0  # 2 min
    # estimated duration: warmup 600 + 3*(180+120) + cooldown 300 = 1800
    assert wj["estimatedDurationInSecs"] == 1800


def test_no_warmup_no_cooldown_no_recovery_shape():
    wj = _build_simple_interval_workout(**_base(work_minutes=5))
    segment = wj["workoutSegments"][0]
    steps = segment["workoutSteps"]
    # only the repeat group, no warmup/cooldown steps
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["repeat"]
    repeat_group = steps[0]
    inner_keys = [s["stepType"]["stepTypeKey"] for s in repeat_group["workoutSteps"]]
    assert inner_keys == ["interval"]  # no recovery step when recovery_minutes == 0


def test_distance_based_interval_uses_distance_end_condition():
    wj = _build_simple_interval_workout(**_base(work_distance_meters=1000, recovery_minutes=1))
    repeat_group = wj["workoutSegments"][0]["workoutSteps"][0]
    work_step = repeat_group["workoutSteps"][0]
    assert work_step["endCondition"]["conditionTypeKey"] == "distance"
    assert work_step["endConditionValue"] == 1000.0


def test_heart_rate_zone_target_sets_zone_number():
    wj = _build_simple_interval_workout(
        **_base(work_minutes=3, work_target="heart_rate_zone", work_zone_number=4)
    )
    work_step = wj["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
    assert work_step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert work_step["zoneNumber"] == 4


def test_power_zone_target_sets_zone_number():
    wj = _build_simple_interval_workout(
        **_base(work_minutes=3, work_target="power_zone", work_zone_number=2)
    )
    work_step = wj["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
    assert work_step["targetType"]["workoutTargetTypeKey"] == "power.zone"
    assert work_step["zoneNumber"] == 2


def test_pace_target_brackets_the_requested_pace():
    # 4:30/km center pace, +/-3% tolerance.
    wj = _build_simple_interval_workout(
        **_base(
            work_distance_meters=1000,
            work_target="pace",
            work_pace_min_per_km=4.5,
            work_pace_tolerance_pct=3.0,
        )
    )
    work_step = wj["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]
    assert work_step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    lower, upper = work_step["targetValueOne"], work_step["targetValueTwo"]
    assert lower < upper
    center = 1000 / (4.5 * 60)
    assert lower == pytest.approx(center * 0.97, rel=1e-6)
    assert upper == pytest.approx(center * 1.03, rel=1e-6)


def test_cycling_sport_type():
    wj = _build_simple_interval_workout(**_base(sport="cycling", work_minutes=5))
    assert wj["sportType"] == {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2}


def test_description_is_passed_through():
    wj = _build_simple_interval_workout(**_base(work_minutes=5, description="race prep"))
    assert wj["description"] == "race prep"
