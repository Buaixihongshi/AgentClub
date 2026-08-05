import sqlite3
from datetime import datetime

from backend.app.domain.models import Conversation, Message, MessageRole, Mission, MissionStatus
from backend.app.storage.repository import Repository


def test_repository_creates_and_fetches_mission(repository):
    mission = Mission(id="m_1", title="Plan", goal="Create a plan")

    repository.create_mission(mission)
    fetched = repository.get_mission("m_1")

    assert fetched is not None
    assert fetched.id == "m_1"
    assert fetched.status == MissionStatus.PENDING


def test_repository_appends_message_and_event(repository):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Create a plan"))
    message = Message(
        id="msg_1",
        mission_id="m_1",
        role=MessageRole.AGENT,
        agent_id="chulan",
        turn_id="t_1",
        provider_id="fake",
        content="老板，我先拆一下任务。",
    )

    repository.add_message(message)
    messages = repository.list_messages("m_1")
    events = repository.list_events_after("m_1", after_id=0)

    assert [m.content for m in messages] == ["老板，我先拆一下任务。"]
    assert events[-1].event_type == "message"
    assert events[-1].payload["content"] == "老板，我先拆一下任务。"


def test_repository_returns_persisted_event_created_at(repository, temp_settings):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Create a plan"))

    with sqlite3.connect(temp_settings.database_path) as db:
        raw_created_at = db.execute("SELECT created_at FROM events WHERE id = 1").fetchone()[0]

    event = repository.list_events_after("m_1", after_id=0)[0]

    assert event.created_at == datetime.fromisoformat(raw_created_at)


def test_repository_filters_events_after_id(repository):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Create a plan"))
    first_event = repository.list_events_after("m_1", after_id=0)[0]
    message = Message(
        id="msg_1",
        mission_id="m_1",
        role=MessageRole.AGENT,
        agent_id="chulan",
        turn_id="t_1",
        provider_id="fake",
        content="老板，我先拆一下任务。",
    )

    repository.add_message(message)
    events = repository.list_events_after("m_1", after_id=int(first_event.id))

    assert len(events) == 1
    assert events[0].event_type == "message"


def test_repository_reopens_existing_database(repository, temp_settings):
    mission = Mission(id="m_1", title="Plan", goal="Create a plan")
    message = Message(
        id="msg_1",
        mission_id="m_1",
        role=MessageRole.AGENT,
        agent_id="chulan",
        turn_id="t_1",
        provider_id="fake",
        content="老板，我先拆一下任务。",
    )
    repository.create_mission(mission)
    repository.add_message(message)

    reopened = Repository(temp_settings.database_path)
    reopened.initialize()

    assert reopened.get_mission("m_1") == mission
    assert reopened.list_messages("m_1") == [message]
    assert [event.event_type for event in reopened.list_events_after("m_1", after_id=0)] == [
        "mission_created",
        "message",
    ]


def test_creates_and_fetches_conversation(repository):
    conv = Conversation(id="c_test1", title="测试对话")
    repository.create_conversation(conv)
    fetched = repository.get_conversation("c_test1")
    assert fetched is not None
    assert fetched.id == "c_test1"
    assert fetched.title == "测试对话"


def test_list_conversations_newest_first(repository):
    import time
    c1 = Conversation(id="c_old", title="旧对话")
    repository.create_conversation(c1)
    time.sleep(0.01)
    c2 = Conversation(id="c_new", title="新对话")
    repository.create_conversation(c2)
    updated = c2.model_copy(update={"updated_at": c2.updated_at})
    repository.update_conversation(updated)

    convs = repository.list_conversations()
    assert len(convs) == 2
    assert convs[0].id == "c_new"


def test_list_missions_for_conversation_isolation(repository):
    repository.create_conversation(Conversation(id="c_a", title="A"))
    repository.create_conversation(Conversation(id="c_b", title="B"))
    repository.create_mission(Mission(id="m_a1", title="A1", goal="goal_a", conversation_id="c_a"))
    repository.create_mission(Mission(id="m_b1", title="B1", goal="goal_b", conversation_id="c_b"))
    repository.create_mission(Mission(id="m_a2", title="A2", goal="goal_a2", conversation_id="c_a"))

    missions_a = repository.list_missions_for_conversation("c_a")
    missions_b = repository.list_missions_for_conversation("c_b")
    assert [m.id for m in missions_a] == ["m_a1", "m_a2"]
    assert [m.id for m in missions_b] == ["m_b1"]


def test_list_messages_for_conversation_isolation(repository):
    repository.create_conversation(Conversation(id="c_a", title="A"))
    repository.create_conversation(Conversation(id="c_b", title="B"))
    repository.create_mission(Mission(id="m_a", title="A", goal="g", conversation_id="c_a"))
    repository.create_mission(Mission(id="m_b", title="B", goal="g", conversation_id="c_b"))
    repository.add_message(Message(
        id="msg_a", mission_id="m_a", conversation_id="c_a",
        role=MessageRole.USER, content="hello a",
    ))
    repository.add_message(Message(
        id="msg_b", mission_id="m_b", conversation_id="c_b",
        role=MessageRole.USER, content="hello b",
    ))

    msgs_a = repository.list_messages_for_conversation("c_a")
    msgs_b = repository.list_messages_for_conversation("c_b")
    assert len(msgs_a) == 1
    assert msgs_a[0].content == "hello a"
    assert len(msgs_b) == 1
    assert msgs_b[0].content == "hello b"


def test_list_events_for_turn_filters_by_turn_id(repository):
    repository.create_mission(Mission(id="m_filter1", title="t", goal="g"))

    repository.add_event("m_filter1", "noise", {"turn_id": "turn_other"})
    repository.add_event("m_filter1", "message", {"turn_id": "turn_target", "content": "hello"})
    repository.add_event("m_filter1", "tool.call", {"turn_id": "turn_target", "tool_name": "x"})
    repository.add_event("m_filter1", "noise2", {})

    events = repository.list_events_for_turn("m_filter1", "turn_target")
    assert len(events) == 2
    assert {e.event_type for e in events} == {"message", "tool.call"}
