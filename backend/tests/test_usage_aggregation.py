"""Tests for _derive_usage_summary in mission_detail."""

from backend.app.api.mission_detail import _derive_usage_summary
from backend.app.domain.models import MissionEvent


def _make_usage_event(
    event_id,
    mission_id: str,
    agent_id: str,
    provider_id: str,
    turn_id: str,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    total_cost_usd=None,
) -> MissionEvent:
    usage = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
    }
    if total_cost_usd is not None:
        usage["total_cost_usd"] = total_cost_usd
    return MissionEvent(
        id=str(event_id),
        mission_id=mission_id,
        event_type="turn.usage",
        payload={
            "agent_id": agent_id,
            "provider_id": provider_id,
            "turn_id": turn_id,
            "usage": usage,
        },
    )


def test_aggregation_empty_when_no_usage_events():
    events = [
        MissionEvent(id="1", mission_id="m1", event_type="turn.started", payload={}),
        MissionEvent(id="2", mission_id="m1", event_type="turn.completed", payload={}),
    ]
    result = _derive_usage_summary(events)
    assert result["mission_total"] is None
    assert result["by_agent"] == []
    assert result["by_provider"] == []
    assert result["by_turn"] == []


def test_aggregation_sums_per_agent():
    events = [
        _make_usage_event(1, "m1", "chulan", "claude", "t1", input_tokens=100, output_tokens=20),
        _make_usage_event(2, "m1", "chulan", "claude", "t2", input_tokens=200, output_tokens=30),
        _make_usage_event(3, "m1", "baobao", "codex", "t3", input_tokens=50, output_tokens=10),
    ]
    result = _derive_usage_summary(events)
    by_agent = {a["agent_id"]: a for a in result["by_agent"]}
    assert len(by_agent) == 2
    assert by_agent["chulan"]["input_tokens"] == 300
    assert by_agent["chulan"]["output_tokens"] == 50
    assert by_agent["baobao"]["input_tokens"] == 50
    assert by_agent["baobao"]["output_tokens"] == 10


def test_aggregation_sums_per_provider():
    events = [
        _make_usage_event(1, "m1", "chulan", "claude", "t1", input_tokens=100, output_tokens=20),
        _make_usage_event(2, "m1", "baobao", "codex", "t2", input_tokens=50, output_tokens=10),
        _make_usage_event(3, "m1", "wangye", "claude", "t3", input_tokens=80, output_tokens=15),
    ]
    result = _derive_usage_summary(events)
    by_provider = {p["provider_id"]: p for p in result["by_provider"]}
    assert len(by_provider) == 2
    assert by_provider["claude"]["input_tokens"] == 180
    assert by_provider["claude"]["output_tokens"] == 35
    assert by_provider["codex"]["input_tokens"] == 50


def test_aggregation_mission_total_sums_all():
    events = [
        _make_usage_event(1, "m1", "chulan", "claude", "t1", input_tokens=100, output_tokens=20, total_cost_usd=0.05),
        _make_usage_event(2, "m1", "baobao", "codex", "t2", input_tokens=50, output_tokens=10),
        _make_usage_event(3, "m1", "wangye", "claude", "t3", input_tokens=80, output_tokens=15, total_cost_usd=0.03),
    ]
    result = _derive_usage_summary(events)
    total = result["mission_total"]
    assert total is not None
    assert total["input_tokens"] == 230
    assert total["output_tokens"] == 45
    assert total["total_cost_usd"] == pytest.approx(0.08)


def test_aggregation_per_turn_preserves_order():
    events = [
        _make_usage_event(1, "m1", "chulan", "claude", "t1", input_tokens=100),
        _make_usage_event(2, "m1", "baobao", "codex", "t2", input_tokens=50),
        _make_usage_event(3, "m1", "wangye", "gemini", "t3", input_tokens=80),
    ]
    result = _derive_usage_summary(events)
    by_turn = result["by_turn"]
    assert len(by_turn) == 3
    assert by_turn[0]["turn_id"] == "t1"
    assert by_turn[0]["agent_id"] == "chulan"
    assert by_turn[1]["turn_id"] == "t2"
    assert by_turn[2]["turn_id"] == "t3"


import pytest
