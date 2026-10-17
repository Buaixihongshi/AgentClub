from backend.app.api.mission_detail import build_mission_detail
from backend.app.domain.models import Conversation, Message, MessageRole, Mission, MissionStatus
from backend.app.main import create_app
from fastapi.testclient import TestClient


def seed_failure(repo, *, published=False):
    repo.create_conversation(Conversation(id="c_failure", title="failure"))
    mission = Mission(id="m_failure", title="first", goal="first", conversation_id="c_failure", status=MissionStatus.BLOCKED)
    repo.create_mission(mission)
    user = Message(id="user_first", mission_id=mission.id, conversation_id="c_failure", role=MessageRole.USER, content="先执行")
    repo.add_message(user)
    repo.add_event(mission.id, "turn.started", {"turn_id": "t_failure", "agent_id": "wangye", "provider_id": "antigravity"})
    if published:
        repo.add_message(Message(id="reply", mission_id=mission.id, role=MessageRole.AGENT, content="部分结果", turn_id="t_failure"))
    repo.add_event(mission.id, "turn.blocked", {"turn_id": "t_failure", "agent_id": "wangye", "provider_id": "antigravity", "error": "provider internal timeout before MCP publish_message"})
    return mission, user


def test_failure_survives_followup_refresh_and_sse_without_entering_message_history(repository, temp_settings):
    mission, user = seed_failure(repository)
    first = build_mission_detail(repository, temp_settings, mission.id)
    assert len(first["failure_events"]) == 1
    failure = first["failure_events"][0]
    assert failure["content"] == "王也本轮超时，未发布回复。"
    from datetime import datetime
    assert datetime.fromisoformat(failure["created_at"]) >= user.created_at
    repository.create_mission(Mission(id="m_followup", title="next", goal="next", conversation_id="c_failure", status=MissionStatus.COMPLETED))
    repository.add_message(Message(id="user_next", mission_id="m_followup", role=MessageRole.USER, content="继续"))
    with TestClient(create_app(temp_settings)) as client:
        for _ in range(2):
            detail = client.get("/api/conversations/c_failure").json()
            assert detail["latest_mission_detail"]["mission"]["id"] == "m_followup"
            assert detail["failure_events"] == [failure]
            assert all(m["role"] != "system" for m in detail["messages"])
        stream = client.get(f"/api/missions/{mission.id}/stream").text
        assert failure["id"] in stream
    assert all(m.role != MessageRole.SYSTEM for m in repository.list_messages(mission.id))


def test_successful_fallback_does_not_leave_failure_notice(repository, temp_settings):
    mission, _ = seed_failure(repository)
    repository.add_event(mission.id, "turn.completed", {"turn_id": "t_failure", "agent_id": "wangye", "provider_id": "codex", "attempt": 2})
    repository.update_mission(mission.model_copy(update={"status": MissionStatus.COMPLETED}))
    assert build_mission_detail(repository, temp_settings, mission.id)["failure_events"] == []


def test_failure_keeps_already_published_reply(repository, temp_settings):
    mission, _ = seed_failure(repository, published=True)
    detail = build_mission_detail(repository, temp_settings, mission.id)
    assert detail["failure_events"][0]["content"] == "王也本轮超时，已发布的回复已保留。"
    assert any(m["content"] == "部分结果" for m in detail["messages"])


def test_no_false_failure_during_fallback_and_runner_failure_without_turn(repository, temp_settings):
    mission, _ = seed_failure(repository)
    repository.update_mission(mission.model_copy(update={"status": MissionStatus.RUNNING}))
    assert build_mission_detail(repository, temp_settings, mission.id)["failure_events"] == []
    repository.create_mission(Mission(id="m_crash", title="crash", goal="crash", status=MissionStatus.FAILED))
    failure = build_mission_detail(repository, temp_settings, "m_crash")["failure_events"][0]
    assert failure["event_type"] == "mission.failure"
