from backend.app.domain.models import Mission
from backend.app.orchestration.events import (
    fetch_a2a_requests_for_turn,
    record_a2a_cancelled,
    record_a2a_invoke,
    record_a2a_reply,
    record_a2a_reply_failed,
    record_a2a_request,
    record_turn_blocked,
    record_turn_completed,
    record_turn_failed,
    record_turn_started,
)


def test_record_turn_started_writes_event(repository):
    mission = Mission(id="m_test1", title="test", goal="test turn events")
    repository.create_mission(mission)

    record_turn_started(
        repository,
        mission_id=mission.id,
        turn_id="turn_1_abc",
        agent_id="chulan",
        provider_id="claude",
        attempt=1,
        purpose="拆解任务",
    )

    events = repository.list_events_after(mission.id, 0)
    turn_events = [e for e in events if e.event_type == "turn.started"]
    assert len(turn_events) == 1
    payload = turn_events[0].payload
    assert payload["turn_id"] == "turn_1_abc"
    assert payload["agent_id"] == "chulan"
    assert payload["provider_id"] == "claude"
    assert payload["attempt"] == 1
    assert payload["purpose"] == "拆解任务"
    assert "started_at" in payload


def test_record_turn_completed_writes_event(repository):
    mission = Mission(id="m_test2", title="test", goal="test turn events")
    repository.create_mission(mission)

    record_turn_completed(
        repository,
        mission_id=mission.id,
        turn_id="turn_1_abc",
        agent_id="chulan",
        provider_id="gemini",
        attempt=2,
        duration_ms=42130,
        summary="fallback provider completed",
    )

    events = repository.list_events_after(mission.id, 0)
    turn_events = [e for e in events if e.event_type == "turn.completed"]
    assert len(turn_events) == 1
    payload = turn_events[0].payload
    assert payload["turn_id"] == "turn_1_abc"
    assert payload["provider_id"] == "gemini"
    assert payload["attempt"] == 2
    assert payload["duration_ms"] == 42130
    assert payload["summary"] == "fallback provider completed"


def test_record_turn_blocked_writes_event(repository):
    mission = Mission(id="m_test3", title="test", goal="test turn events")
    repository.create_mission(mission)

    record_turn_blocked(
        repository,
        mission_id=mission.id,
        turn_id="turn_1_abc",
        agent_id="chulan",
        provider_id="claude",
        attempt=1,
        duration_ms=900,
        error="provider is disabled: claude",
    )

    events = repository.list_events_after(mission.id, 0)
    turn_events = [e for e in events if e.event_type == "turn.blocked"]
    assert len(turn_events) == 1
    payload = turn_events[0].payload
    assert payload["error"] == "provider is disabled: claude"
    assert payload["duration_ms"] == 900


def test_record_turn_failed_writes_event(repository):
    mission = Mission(id="m_test4", title="test", goal="test turn events")
    repository.create_mission(mission)

    record_turn_failed(
        repository,
        mission_id=mission.id,
        turn_id="turn_1_abc",
        agent_id="chulan",
        provider_id="claude",
        attempt=1,
        duration_ms=500,
        error="Adapter failed: connection timeout",
    )

    events = repository.list_events_after(mission.id, 0)
    turn_events = [e for e in events if e.event_type == "turn.failed"]
    assert len(turn_events) == 1
    payload = turn_events[0].payload
    assert payload["error"] == "Adapter failed: connection timeout"


def test_record_a2a_invoke_writes_event(repository):
    mission = Mission(id="m_a2a1", title="test", goal="test a2a")
    repository.create_mission(mission)

    record_a2a_invoke(
        repository,
        mission_id=mission.id,
        sender_agent_id="chulan",
        sender_turn_id="turn_1_abc",
        target_agent_id="baobao",
        purpose="核对异闻物档案",
        mode="fire_and_forget",
        queued_position=1,
    )

    events = repository.list_events_after(mission.id, 0)
    a2a = [event for event in events if event.event_type == "a2a.invoke"]
    assert len(a2a) == 1
    assert a2a[0].payload == {
        "mission_id": mission.id,
        "sender_agent_id": "chulan",
        "sender_turn_id": "turn_1_abc",
        "target_agent_id": "baobao",
        "purpose": "核对异闻物档案",
        "mode": "fire_and_forget",
        "queued_position": 1,
    }


def test_record_a2a_cancelled_writes_event(repository):
    mission = Mission(id="m_a2a2", title="test", goal="test a2a cancel")
    repository.create_mission(mission)

    record_a2a_cancelled(
        repository,
        mission_id=mission.id,
        pending_count=2,
        reason="interrupt",
    )

    events = repository.list_events_after(mission.id, 0)
    cancelled = [event for event in events if event.event_type == "a2a.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload["pending_count"] == 2
    assert cancelled[0].payload["reason"] == "interrupt"


def test_record_a2a_request_writes_event(repository):
    mission = Mission(id="m_test6", title="test", goal="test a2a request")
    repository.create_mission(mission)

    record_a2a_request(
        repository,
        mission.id,
        sender_agent_id="chulan",
        sender_turn_id="turn_1_abc",
        target_agent_id="baobao",
        purpose="核对档案",
        mode="fire_and_forget",
    )

    events = repository.list_events_after(mission.id, 0)
    a2a_req = [e for e in events if e.event_type == "a2a.request"]
    assert len(a2a_req) == 1
    assert a2a_req[0].payload["sender_agent_id"] == "chulan"
    assert a2a_req[0].payload["target_agent_id"] == "baobao"
    assert a2a_req[0].payload["sender_turn_id"] == "turn_1_abc"


def test_fetch_a2a_requests_for_turn(repository):
    mission = Mission(id="m_test7", title="test", goal="test fetch a2a requests")
    repository.create_mission(mission)

    record_a2a_request(repository, mission.id, "chulan", "turn_A", "baobao", "任务A", "fire_and_forget")
    record_a2a_request(repository, mission.id, "baobao", "turn_B", "wangye", "任务B", "fire_and_forget")

    results_A = fetch_a2a_requests_for_turn(repository, mission.id, "turn_A")
    assert len(results_A) == 1
    assert results_A[0].payload["target_agent_id"] == "baobao"

    results_B = fetch_a2a_requests_for_turn(repository, mission.id, "turn_B")
    assert len(results_B) == 1
    assert results_B[0].payload["target_agent_id"] == "wangye"

    results_none = fetch_a2a_requests_for_turn(repository, mission.id, "turn_X")
    assert results_none == []


def test_multiple_turn_events_for_fallback_scenario(repository):
    mission = Mission(id="m_test5", title="test", goal="test fallback")
    repository.create_mission(mission)

    record_turn_started(repository, mission.id, "turn_1_x", "chulan", "claude", 1, "拆解")
    record_turn_blocked(repository, mission.id, "turn_1_x", "chulan", "claude", 1, 50, "disabled")
    record_turn_started(repository, mission.id, "turn_1_x", "chulan", "gemini", 2, "拆解")
    record_turn_completed(repository, mission.id, "turn_1_x", "chulan", "gemini", 2, 5000, "ok")

    events = repository.list_events_after(mission.id, 0)
    turn_types = [e.event_type for e in events if e.event_type.startswith("turn.")]
    assert turn_types == ["turn.started", "turn.blocked", "turn.started", "turn.completed"]


def test_record_a2a_reply_persists_payload(repository):
    repository.create_mission(Mission(id="m_reply1", title="t", goal="g"))
    record_a2a_reply(
        repository,
        "m_reply1",
        callee_turn_id="turn_callee_1",
        callee_agent_id="baobao",
        invoke_event_id="42",
        sender_agent_id="chulan",
        sender_resume_step_id="abcd1234",
        reply_text="档案没有此条目。",
        reply_message_id="msg_99",
        truncated=False,
    )

    events = repository.list_events_after("m_reply1", 0)
    reply_events = [e for e in events if e.event_type == "a2a.reply"]
    assert len(reply_events) == 1
    payload = reply_events[0].payload
    assert payload["sender_agent_id"] == "chulan"
    assert payload["sender_resume_step_id"] == "abcd1234"
    assert payload["reply_text"] == "档案没有此条目。"
    assert payload["reply_message_id"] == "msg_99"
    assert payload["truncated"] is False
    assert payload["invoke_event_id"] == "42"


def test_record_a2a_reply_failed_persists_payload(repository):
    repository.create_mission(Mission(id="m_reply2", title="t", goal="g"))
    record_a2a_reply_failed(
        repository,
        "m_reply2",
        callee_turn_id="turn_callee_2",
        callee_agent_id="wangye",
        invoke_event_id="7",
        reason="callee turn blocked",
        fallback_reply_text="<no reply: turn blocked>",
    )

    events = repository.list_events_after("m_reply2", 0)
    failed = [e for e in events if e.event_type == "a2a.reply.failed"]
    assert len(failed) == 1
    assert failed[0].payload["reason"] == "callee turn blocked"
    assert failed[0].payload["fallback_reply_text"] == "<no reply: turn blocked>"


def test_record_a2a_cancelled_includes_cancelled_steps(repository):
    repository.create_mission(Mission(id="m_cancel1", title="t", goal="g"))
    record_a2a_cancelled(
        repository,
        "m_cancel1",
        pending_count=2,
        reason="interrupt",
        cancelled_steps=[
            {"step_id": "aaa", "origin": "a2a", "agent_id": "baobao"},
            {"step_id": "bbb", "origin": "a2a_resume", "agent_id": "chulan"},
        ],
    )

    events = repository.list_events_after("m_cancel1", 0)
    cancelled = [e for e in events if e.event_type == "a2a.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].payload["pending_count"] == 2
    assert cancelled[0].payload["cancelled_steps"] == [
        {"step_id": "aaa", "origin": "a2a", "agent_id": "baobao"},
        {"step_id": "bbb", "origin": "a2a_resume", "agent_id": "chulan"},
    ]
