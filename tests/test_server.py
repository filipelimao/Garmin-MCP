"""Offline tests for the hybrid Garmin Connect MCP server.

These never touch the real Garmin Connect API or your credentials.
FakeOfficialClient / FakeUnofficialClient stand in for the two backends
and record every call they receive, so each test only checks that a
tool calls the right underlying method with the right arguments, and
that errors come back as a normal tool result instead of crashing the
server.

Run with: pytest
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from garmin_personal_api import HttpError

from garmin_mcp import auth_official, auth_unofficial, server


class _RecordingFake:
    """Records every call made to it and echoes the call back as the result."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return {"ok": True, "method": name, "args": args, "kwargs": kwargs}

    def __getattr__(self, name):
        def _method(*args, **kwargs):
            return self._record(name, *args, **kwargs)

        return _method


class FakeOfficialClient(_RecordingFake):
    def close(self):
        pass


class FakeUnofficialClient(_RecordingFake):
    pass


@pytest.fixture
def fake_official(monkeypatch):
    client = FakeOfficialClient()
    monkeypatch.setattr(auth_official, "_client", client)
    yield client
    monkeypatch.setattr(auth_official, "_client", None)


@pytest.fixture
def fake_unofficial(monkeypatch):
    client = FakeUnofficialClient()
    monkeypatch.setattr(auth_unofficial, "_client", client)
    yield client
    monkeypatch.setattr(auth_unofficial, "_client", None)


async def _call(tool_name: str, **kwargs):
    result = await server.mcp.call_tool(tool_name, kwargs)
    return json.loads(result.content[0].text)


TODAY = date.today().isoformat()


# --------------------------------------------------------------------------
# Official API tools
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_api_key_returns_error_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(auth_official, "_client", None)
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "")
    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY", raising=False)
    monkeypatch.setattr(
        "garmin_personal_api.DEFAULT_API_KEY_FILE", str(tmp_path / "does-not-exist")
    )
    out = await _call("list_activities")
    assert "error" in out
    assert "garmin-mcp-save-key" in out["error"]


@pytest.mark.asyncio
async def test_list_activities_defaults(fake_official):
    out = await _call("list_activities")
    assert out["method"] == "search_activities"
    assert out["kwargs"] == {
        "start": 0,
        "limit": 20,
        "start_date": None,
        "end_date": None,
        "activity_type": None,
    }


@pytest.mark.asyncio
async def test_list_activities_with_filters(fake_official):
    out = await _call("list_activities", start=10, limit=5, activity_type="cycling")
    assert out["kwargs"]["start"] == 10
    assert out["kwargs"]["limit"] == 5
    assert out["kwargs"]["activity_type"] == "cycling"


@pytest.mark.asyncio
async def test_get_activity_passes_id(fake_official):
    out = await _call("get_activity", activity_id="123456789")
    assert out["method"] == "get_activity"
    assert out["args"] == ["123456789"]


@pytest.mark.asyncio
async def test_range_tools_default_start_and_end_to_today(fake_official):
    for tool, method in [
        ("get_daily_summary", "get_daily_summaries"),
        ("get_steps", "get_daily_steps"),
        ("get_stress", "get_daily_stress"),
        ("get_hydration", "get_daily_hydration"),
        ("get_weight_measurements", "get_weight_measurements"),
        ("get_weight_goals", "get_effective_weight_goals"),
    ]:
        out = await _call(tool)
        assert out["method"] == method
        assert out["args"] == [TODAY, TODAY]


@pytest.mark.asyncio
async def test_range_tool_end_date_defaults_to_start_date(fake_official):
    out = await _call("get_steps", start_date="2026-01-01")
    assert out["args"] == ["2026-01-01", "2026-01-01"]


@pytest.mark.asyncio
async def test_range_tool_explicit_range(fake_official):
    out = await _call("get_steps", start_date="2026-01-01", end_date="2026-01-07")
    assert out["args"] == ["2026-01-01", "2026-01-07"]


@pytest.mark.asyncio
async def test_single_day_tools_default_to_today(fake_official):
    for tool, method in [
        ("get_sleep", "get_daily_sleep"),
        ("get_heart_rate", "get_daily_heart_rate"),
        ("get_hrv", "get_daily_hrv"),
        ("get_spo2", "get_daily_spo2"),
        ("get_fitness_age", "get_fitness_age"),
        ("get_food_logs", "get_daily_food_logs"),
        ("get_menstrual_cycle_summary", "get_menstrual_cycle_summary"),
    ]:
        out = await _call(tool)
        assert out["method"] == method
        assert out["args"] == [TODAY]


@pytest.mark.asyncio
async def test_menstrual_cycle_day_view_passes_both_dates(fake_official):
    out = await _call("get_menstrual_cycle_day_view", day="2026-02-01")
    assert out["method"] == "get_menstrual_cycle_day_view"
    assert out["args"] == ["2026-02-01", TODAY]


@pytest.mark.asyncio
async def test_body_battery_passes_chunks(fake_official):
    out = await _call("get_body_battery", start_date="2026-01-01", chunks=4)
    assert out["method"] == "get_daily_body_battery"
    assert out["args"] == ["2026-01-01", "2026-01-01"]
    assert out["kwargs"] == {"chunks": 4}


@pytest.mark.asyncio
async def test_no_arg_official_tools(fake_official):
    for tool, method in [
        ("get_heart_rate_zones", "get_heart_rate_zones"),
        ("get_profile_settings", "get_user_profile_settings"),
    ]:
        out = await _call(tool)
        assert out["method"] == method
        assert out["args"] == []


@pytest.mark.asyncio
async def test_official_401_triggers_one_retry_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    class FlakyOnceClient(FakeOfficialClient):
        def search_activities(self, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise HttpError(status_code=401, method="GET", url="https://x", response_text="expired")
            return self._record("search_activities", **kwargs)

    client_holder = {"client": FlakyOnceClient()}
    monkeypatch.setattr(auth_official, "_client", client_holder["client"])

    def fake_get_client():
        if auth_official._client is None:
            auth_official._client = client_holder["client"]
        return auth_official._client

    monkeypatch.setattr(auth_official, "get_client", fake_get_client)

    out = await _call("list_activities")
    assert out["method"] == "search_activities"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_official_api_exception_becomes_error_result(fake_official, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(fake_official, "search_activities", boom)
    out = await _call("list_activities")
    assert "error" in out
    assert "simulated failure" in out["error"]


# --------------------------------------------------------------------------
# Unofficial fallback tools
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_cached_session_returns_error_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(auth_unofficial, "_client", None)
    monkeypatch.setattr(auth_unofficial, "DEFAULT_TOKENSTORE", str(tmp_path / "does-not-exist"))
    out = await _call("get_training_status")
    assert "error" in out
    assert "garmin-mcp-auth" in out["error"]


@pytest.mark.asyncio
async def test_unofficial_tools_dispatch_correctly(fake_unofficial):
    for tool, method in [
        ("get_respiration", "get_respiration_data"),
        ("get_training_status", "get_training_status"),
        ("get_training_readiness", "get_training_readiness"),
        ("get_max_metrics", "get_max_metrics"),
    ]:
        out = await _call(tool)
        assert out["method"] == method
        assert out["args"] == [TODAY]


@pytest.mark.asyncio
async def test_unofficial_no_arg_tools(fake_unofficial):
    for tool, method in [
        ("get_race_predictions", "get_race_predictions"),
        ("get_personal_records", "get_personal_record"),
        ("list_devices", "get_devices"),
    ]:
        out = await _call(tool)
        assert out["method"] == method
        assert out["args"] == []


@pytest.mark.asyncio
async def test_unofficial_activity_detail_tools_pass_id(fake_unofficial):
    for tool, method in [
        ("get_activity_splits", "get_activity_splits"),
        ("get_activity_laps", "get_activity_laps"),
        ("get_activity_weather", "get_activity_weather"),
        ("get_activity_gear", "get_activity_gear"),
    ]:
        out = await _call(tool, activity_id="42")
        assert out["method"] == method
        assert out["args"] == ["42"]


@pytest.mark.asyncio
async def test_unofficial_docstrings_are_labeled():
    tools = await server.mcp.list_tools()
    unofficial_names = {
        "get_respiration",
        "get_training_status",
        "get_training_readiness",
        "get_max_metrics",
        "get_race_predictions",
        "get_personal_records",
        "list_devices",
        "get_activity_splits",
        "get_activity_laps",
        "get_activity_weather",
        "get_activity_gear",
    }
    for tool in tools:
        if tool.name in unofficial_names:
            assert tool.description.strip().startswith("(unofficial API)"), tool.name


# --------------------------------------------------------------------------
# Workout tools (write to the account; see tools_workouts.py)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_cached_session_returns_error_not_crash_for_workout_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(auth_unofficial, "_client", None)
    monkeypatch.setattr(auth_unofficial, "DEFAULT_TOKENSTORE", str(tmp_path / "does-not-exist"))
    out = await _call("list_workouts")
    assert "error" in out
    assert "garmin-mcp-auth" in out["error"]


@pytest.mark.asyncio
async def test_workout_no_arg_tools(fake_unofficial):
    for tool, method in [
        ("get_workout_types", "get_workout_types"),
        ("send_scheduled_workouts_to_devices", "send_scheduled_workouts_to_devices"),
    ]:
        out = await _call(tool)
        assert out["method"] == method
        assert out["args"] == []


@pytest.mark.asyncio
async def test_workout_id_tools_pass_id(fake_unofficial):
    for tool, method in [
        ("get_workout", "get_workout_by_id"),
        ("delete_workout", "delete_workout"),
    ]:
        out = await _call(tool, workout_id="999")
        assert out["method"] == method
        assert out["args"] == ["999"]


@pytest.mark.asyncio
async def test_list_workouts_default_limit(fake_unofficial):
    out = await _call("list_workouts")
    assert out["method"] == "get_workouts"
    assert out["kwargs"] == {"limit": 100}


@pytest.mark.asyncio
async def test_schedule_workout_passes_id_and_date(fake_unofficial):
    out = await _call("schedule_workout", workout_id="999", date="2026-11-01")
    assert out["method"] == "schedule_workout"
    assert out["args"] == ["999", "2026-11-01"]


@pytest.mark.asyncio
async def test_push_workout_to_device_defaults(fake_unofficial):
    out = await _call("push_workout_to_device")
    assert out["method"] == "push_workout_to_device"
    assert out["args"] == [None, None]


@pytest.mark.asyncio
async def test_create_workout_passes_raw_json(fake_unofficial):
    workout = {"workoutName": "custom"}
    out = await _call("create_workout", workout_json=workout)
    assert out["method"] == "upload_workout"
    assert out["args"] == [workout]


@pytest.mark.asyncio
async def test_create_simple_interval_workout_uploads_built_json(fake_unofficial):
    out = await _call(
        "create_simple_interval_workout",
        sport="running",
        name="6x800",
        repeats=6,
        work_minutes=3,
        recovery_minutes=2,
        warmup_minutes=10,
        cooldown_minutes=5,
        work_target="heart_rate_zone",
        work_zone_number=4,
    )
    assert out["method"] == "upload_workout"
    (workout_json,) = out["args"]
    assert workout_json["workoutName"] == "6x800"
    assert workout_json["sportType"]["sportTypeKey"] == "running"


@pytest.mark.asyncio
async def test_create_simple_interval_workout_bad_input_is_an_error_not_a_crash(fake_unofficial):
    out = await _call("create_simple_interval_workout", sport="walking", name="x", repeats=1)
    assert "error" in out
    assert "ValueError" in out["error"]


@pytest.mark.asyncio
async def test_workout_docstrings_are_labeled():
    tools = await server.mcp.list_tools()
    workout_names = {
        "get_workout_types",
        "create_simple_interval_workout",
        "create_workout",
        "list_workouts",
        "get_workout",
        "delete_workout",
        "schedule_workout",
        "push_workout_to_device",
        "send_scheduled_workouts_to_devices",
    }
    found = set()
    for tool in tools:
        if tool.name in workout_names:
            found.add(tool.name)
            assert tool.description.strip().startswith("(unofficial API)"), tool.name
    assert found == workout_names


# --------------------------------------------------------------------------
# Overall registration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tools_are_registered():
    tools = await server.mcp.list_tools()
    # 23 official + 11 unofficial-fallback (read-only) + 9 workout tools.
    assert len(tools) == 43
