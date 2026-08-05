from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.domain.models import Conversation, Message, MessageRole, Mission, MissionStatus, TurnStatus
from backend.app.orchestration.events import find_pending_interrupts, record_interrupt_handled
from backend.app.orchestration.runner import MissionRunner


def test_find_pending_interrupts_returns_unhandled(repository, temp_settings):
    mission = Mission(id="m_int1", title="Plan", goal="测试插话")
    repository.create_mission(mission)
    repository.add_event("m_int1", "interrupt", {
        "mission_id": "m_int1",
        "content": "暂停",
        "target_agent_id": "baobao",
    })

    pending = find_pending_interrupts(repository, "m_int1")

    assert len(pending) == 1
    assert pending[0].event_type == "interrupt"
    assert pending[0].payload["content"] == "暂停"


def test_find_pending_interrupts_excludes_handled(repository, temp_settings):
    mission = Mission(id="m_int2", title="Plan", goal="测试插话")
    repository.create_mission(mission)
    repository.add_event("m_int2", "interrupt", {
        "mission_id": "m_int2",
        "content": "暂停",
        "target_agent_id": "baobao",
    })
    events = repository.list_events_after("m_int2", 0)
    interrupt_event = [e for e in events if e.event_type == "interrupt"][0]
    record_interrupt_handled(
        repository, "m_int2", str(interrupt_event.id),
        "baobao", "baobao", "turn_1_xyz",
    )

    pending = find_pending_interrupts(repository, "m_int2")

    assert len(pending) == 0


def test_record_interrupt_handled_writes_event(repository, temp_settings):
    mission = Mission(id="m_int3", title="Plan", goal="测试插话")
    repository.create_mission(mission)
    repository.add_event("m_int3", "interrupt", {
        "mission_id": "m_int3",
        "content": "暂停",
        "target_agent_id": "wangye",
    })

    record_interrupt_handled(
        repository, "m_int3", "99",
        "wangye", "wangye", "turn_2_abc",
    )

    events = repository.list_events_after("m_int3", 0)
    handled = [e for e in events if e.event_type == "interrupt.handled"]
    assert len(handled) == 1
    assert handled[0].payload["interrupt_event_id"] == "99"
    assert handled[0].payload["handled_by_agent_id"] == "wangye"
    assert handled[0].payload["turn_id"] == "turn_2_abc"


class InterruptTestAdapter:
    """Adapter that always completes successfully."""
    def __init__(self):
        self.calls = []

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        self.calls.append({
            "mission_id": mission_id,
            "agent_id": agent_id,
            "turn_id": turn_id,
            "provider_id": provider_id,
            "prompt": prompt,
        })
        tools.publish_message(mission_id, agent_id, turn_id, f"[{agent_id}] ok", provider_id)
        return TurnOutcome(status=TurnStatus.COMPLETED)


def test_runner_handles_interrupt_with_target_agent(repository, temp_settings):
    mission = Mission(id="m_int4", title="Plan", goal="设计方案")
    repository.create_mission(mission)
    repository.add_event("m_int4", "interrupt", {
        "mission_id": "m_int4",
        "content": "让王也先说",
        "target_agent_id": "wangye",
    })

    adapter = InterruptTestAdapter()
    runner = MissionRunner(repository, temp_settings, adapter=adapter)
    temp_settings.max_turns = 1
    runner.run("m_int4")

    messages = repository.list_messages("m_int4")
    assert messages
    assert messages[0].agent_id == "wangye"

    events = repository.list_events_after("m_int4", 0)
    handled = [e for e in events if e.event_type == "interrupt.handled"]
    assert len(handled) == 1
    assert handled[0].payload["handled_by_agent_id"] == "wangye"
    assert handled[0].payload["turn_id"] == messages[0].turn_id
    turn_started = [e for e in events if e.event_type == "turn.started"]
    assert len(turn_started) == 1
    assert handled[0].payload["turn_id"] == turn_started[0].payload["turn_id"]
    assert "让王也先说" in adapter.calls[0]["prompt"]


def test_runner_handles_interrupt_without_target_uses_workflow(repository, temp_settings):
    mission = Mission(id="m_int5", title="Plan", goal="设计方案")
    repository.create_mission(mission)
    repository.add_event("m_int5", "interrupt", {
        "mission_id": "m_int5",
        "content": "继续",
        "target_agent_id": None,
    })

    adapter = InterruptTestAdapter()
    runner = MissionRunner(repository, temp_settings, adapter=adapter)
    temp_settings.max_turns = 1
    runner.run("m_int5")

    messages = repository.list_messages("m_int5")
    assert messages
    events = repository.list_events_after("m_int5", 0)
    handled = [e for e in events if e.event_type == "interrupt.handled"]
    assert len(handled) == 1
    assert handled[0].payload["handled_by_agent_id"] == "chulan"
    assert handled[0].payload["turn_id"] == messages[0].turn_id
    assert "继续" in adapter.calls[0]["prompt"]


def test_runner_handles_interrupt_with_invalid_target_uses_workflow_agent(repository, temp_settings):
    mission = Mission(id="m_int7", title="Plan", goal="设计方案")
    repository.create_mission(mission)
    repository.add_event("m_int7", "interrupt", {
        "mission_id": "m_int7",
        "content": "不存在的人也别崩",
        "target_agent_id": "missing-agent",
    })

    adapter = InterruptTestAdapter()
    runner = MissionRunner(repository, temp_settings, adapter=adapter)
    temp_settings.max_turns = 1
    runner.run("m_int7")

    messages = repository.list_messages("m_int7")
    assert messages
    assert messages[0].agent_id == "chulan"

    events = repository.list_events_after("m_int7", 0)
    handled = [e for e in events if e.event_type == "interrupt.handled"]
    assert len(handled) == 1
    assert handled[0].payload["target_agent_id"] == "missing-agent"
    assert handled[0].payload["handled_by_agent_id"] == "chulan"
    assert handled[0].payload["turn_id"] == messages[0].turn_id
    assert "不存在的人也别崩" in adapter.calls[0]["prompt"]


def test_interrupt_target_overrides_mission_target_agent(repository, temp_settings):
    """When a mission has target_agent_id but a pending interrupt points to a
    different agent, the interrupt's target takes priority."""
    mission = Mission(
        id="m_int8", title="Plan", goal="设计方案",
        target_agent_id="baobao",
    )
    repository.create_mission(mission)
    repository.add_event("m_int8", "interrupt", {
        "mission_id": "m_int8",
        "content": "让王也来",
        "target_agent_id": "wangye",
    })

    adapter = InterruptTestAdapter()
    runner = MissionRunner(repository, temp_settings, adapter=adapter)
    temp_settings.max_turns = 1
    runner.run("m_int8")

    messages = repository.list_messages("m_int8")
    assert messages
    assert messages[0].agent_id == "wangye"

    events = repository.list_events_after("m_int8", 0)
    handled = [e for e in events if e.event_type == "interrupt.handled"]
    assert len(handled) == 1
    assert handled[0].payload["handled_by_agent_id"] == "wangye"


def test_interrupt_prompt_includes_content_with_conversation_context(repository, temp_settings):
    """Even when conversation context is present, interrupt content must appear in prompt."""
    repository.create_conversation(Conversation(id="c_int", title="int"))
    repository.create_mission(Mission(
        id="m_int_prior", title="prior", goal="先做规划", conversation_id="c_int",
    ))
    repository.add_message(Message(
        id="msg_prior", mission_id="m_int_prior", conversation_id="c_int",
        role=MessageRole.USER, content="之前的消息",
    ))

    repository.create_mission(Mission(
        id="m_int9", title="Plan", goal="设计方案", conversation_id="c_int",
    ))
    repository.add_event("m_int9", "interrupt", {
        "mission_id": "m_int9",
        "content": "紧急停止",
        "target_agent_id": "wangye",
    })

    adapter = InterruptTestAdapter()
    runner = MissionRunner(repository, temp_settings, adapter=adapter)
    temp_settings.max_turns = 1
    runner.run("m_int9")

    prompt = adapter.calls[0]["prompt"]
    assert "紧急停止" in prompt
    assert "最近对话" in prompt
    assert "用户：之前的消息" in prompt


def test_runner_completes_normally_without_interrupt(repository, temp_settings):
    mission = Mission(id="m_int6", title="Plan", goal="设计方案")
    repository.create_mission(mission)

    runner = MissionRunner(repository, temp_settings, adapter=FakeCLIAdapter())
    completed = runner.run("m_int6")

    assert completed.status == MissionStatus.COMPLETED
    events = repository.list_events_after("m_int6", 0)
    handled = [e for e in events if e.event_type == "interrupt.handled"]
    assert len(handled) == 0
