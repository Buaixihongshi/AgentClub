from backend.app.adapters.base import TurnOutcome
from backend.app.domain.agent_registry import AgentRegistry
from backend.app.domain.models import (
    Mission,
    MissionStatus,
    OrchestrationConfig,
    OrchestrationMode,
    TaskComplexity,
    TurnStatus,
)
from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.orchestration.runner import MissionRunner
from backend.app.orchestration.strategies import plan_orchestration


AGENTS = AgentRegistry.builtin().all()
PARTICIPANTS = ["chulan", "baobao", "wangye"]


def test_orchestration_config_rejects_duplicate_and_impossible_participant_sets():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OrchestrationConfig(mode=OrchestrationMode.FIXED, participant_ids=["chulan", "chulan"])
    with pytest.raises(ValidationError):
        OrchestrationConfig(mode=OrchestrationMode.SINGLE, participant_ids=["chulan", "baobao"])
    with pytest.raises(ValidationError):
        OrchestrationConfig(mode=OrchestrationMode.FIXED, participant_ids=["chulan"])


def _mission(mode, participants=None):
    return Mission(
        id="m_strategy",
        title="Strategy",
        goal="评估任务",
        complexity=TaskComplexity.COMPLEX,
        orchestration=OrchestrationConfig(
            mode=mode, participant_ids=list(participants or [])
        ),
    )


def test_single_plan_is_one_turn_and_no_a2a():
    plan = plan_orchestration(
        _mission(OrchestrationMode.SINGLE, ["baobao"]),
        AGENTS,
        default_max_a2a=8,
        default_max_turns=6,
    )

    assert plan.mode == OrchestrationMode.SINGLE
    assert [step.agent_id for step in plan.steps] == ["baobao"]
    assert plan.steps[0].is_closing is True
    assert plan.max_turns == 1
    assert plan.a2a_enabled is False


def test_reflection_plan_drafts_audits_then_revises():
    plan = plan_orchestration(
        _mission(OrchestrationMode.REFLECTION, ["wangye"]),
        AGENTS,
        default_max_a2a=8,
        default_max_turns=6,
    )

    assert [step.agent_id for step in plan.steps] == ["wangye"] * 4
    assert [step.is_closing for step in plan.steps] == [False, False, False, True]
    assert "起草" in plan.steps[0].purpose
    assert "检查一" in plan.steps[1].purpose
    assert "检查二" in plan.steps[2].purpose
    assert "修订" in plan.steps[3].purpose
    assert plan.max_turns == 4
    assert plan.a2a_enabled is False


def test_fixed_plan_uses_every_participant_then_lead_closes_without_a2a():
    plan = plan_orchestration(
        _mission(OrchestrationMode.FIXED, PARTICIPANTS),
        AGENTS,
        default_max_a2a=8,
        default_max_turns=6,
    )

    assert [step.agent_id for step in plan.steps] == PARTICIPANTS + [PARTICIPANTS[0]]
    assert plan.steps[-1].is_closing is True
    assert "起草" in plan.steps[0].purpose
    assert "检查一" in plan.steps[1].purpose
    assert "检查二" in plan.steps[2].purpose
    assert "修订" in plan.steps[3].purpose
    assert plan.max_turns == 4
    assert plan.a2a_enabled is False


def test_reflection_and_fixed_share_the_same_stage_purposes():
    reflection = plan_orchestration(
        _mission(OrchestrationMode.REFLECTION, ["chulan"]),
        AGENTS,
        default_max_a2a=0,
        default_max_turns=6,
    )
    fixed = plan_orchestration(
        _mission(OrchestrationMode.FIXED, PARTICIPANTS),
        AGENTS,
        default_max_a2a=0,
        default_max_turns=6,
    )

    assert [step.purpose for step in reflection.steps] == [
        step.purpose for step in fixed.steps
    ]
    assert len(reflection.steps) == len(fixed.steps) == 4


def test_dynamic_plan_uses_the_same_skeleton_as_fixed_and_enables_a2a():
    plan = plan_orchestration(
        _mission(OrchestrationMode.DYNAMIC, PARTICIPANTS),
        AGENTS,
        default_max_a2a=3,
        default_max_turns=6,
    )

    assert [step.agent_id for step in plan.steps] == PARTICIPANTS + [PARTICIPANTS[0]]
    assert plan.steps[-1].is_closing is True
    assert plan.max_turns == 4
    assert plan.a2a_enabled is True
    assert plan.max_a2a_invocations == 2
    assert set(plan.agent_ids) == set(PARTICIPANTS)


def test_unknown_participant_reports_available_ids():
    try:
        plan_orchestration(
            _mission(OrchestrationMode.FIXED, ["chulan", "missing"]),
            AGENTS,
            default_max_a2a=0,
            default_max_turns=6,
        )
    except ValueError as exc:
        assert "missing" in str(exc)
        assert "chulan" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected ValueError")


class RecordingAdapter:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        self.calls.append({"agent_id": agent_id, "prompt": prompt, "turn_id": turn_id})
        if self.callback is not None:
            self.callback(self, mission_id, agent_id, turn_id, provider_id, prompt, tools)
        tools.publish_message(
            mission_id,
            agent_id,
            turn_id,
            "回复 {n}".format(n=len(self.calls)),
            provider_id,
        )
        return TurnOutcome(status=TurnStatus.COMPLETED)


def _create_mission(repository, mode, participants, settings):
    mission = Mission(
        id="m_run_strategy",
        title="Run strategy",
        goal="请独立给出答案",
        room_mode="task",
        orchestration=OrchestrationConfig(mode=mode, participant_ids=participants),
    )
    repository.create_mission(mission)
    return mission


def test_reflection_runner_executes_three_self_turns(repository, temp_settings):
    mission = _create_mission(
        repository, OrchestrationMode.REFLECTION, ["chulan"], temp_settings
    )
    adapter = RecordingAdapter()

    result = MissionRunner(repository, temp_settings, adapter).run(mission.id)

    assert result.status == MissionStatus.COMPLETED
    assert len(adapter.calls) == 4
    assert {call["agent_id"] for call in adapter.calls} == {"chulan"}
    assert "起草" in adapter.calls[0]["prompt"]
    assert "检查一" in adapter.calls[1]["prompt"]
    assert "检查二" in adapter.calls[2]["prompt"]
    assert "修订" in adapter.calls[3]["prompt"]
    assert "回复 1" in adapter.calls[1]["prompt"]
    assert "回复 2" in adapter.calls[2]["prompt"]
    assert "回复 3" in adapter.calls[3]["prompt"]
    assert "invoke_agent" not in adapter.calls[0]["prompt"]
    messages = repository.list_messages(mission.id)
    assert messages[-1].content == "回复 4"
    events = repository.list_events_after(mission.id, 0)
    assert not [e for e in events if e.event_type == "a2a.invoke"]


def test_single_runner_executes_one_turn(repository, temp_settings):
    mission = _create_mission(
        repository, OrchestrationMode.SINGLE, ["baobao"], temp_settings
    )
    adapter = RecordingAdapter()

    result = MissionRunner(repository, temp_settings, adapter).run(mission.id)

    assert result.status == MissionStatus.COMPLETED
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["agent_id"] == "baobao"
    assert "invoke_agent" not in adapter.calls[0]["prompt"]
    assert "三位伙伴" not in adapter.calls[0]["prompt"]


def test_dynamic_runner_consumes_a2a_before_closing(repository, temp_settings):
    temp_settings.max_a2a_invocations = 3
    mission = _create_mission(
        repository, OrchestrationMode.DYNAMIC, PARTICIPANTS, temp_settings
    )
    seen = []

    def callback(adapter, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        seen.append(agent_id)
        if agent_id == "wangye" and "检查二" in prompt:
            tools.invoke_agent(
                mission_id, agent_id, turn_id, "baobao", "复核一个边界条件", provider_id
            )

    adapter = RecordingAdapter(callback)
    result = MissionRunner(repository, temp_settings, adapter).run(mission.id)

    assert result.status == MissionStatus.COMPLETED
    assert seen == ["chulan", "baobao", "wangye", "baobao", "chulan"]
    events = repository.list_events_after(mission.id, 0)
    invokes = [e for e in events if e.event_type == "a2a.invoke"]
    assert len(invokes) == 1
    assert "invoke_agent" in adapter.calls[0]["prompt"]
    assert repository.list_messages(mission.id)[-1].content == "回复 5"


def test_fixed_runner_ignores_stray_a2a_calls(repository, temp_settings):
    mission = _create_mission(
        repository, OrchestrationMode.FIXED, PARTICIPANTS, temp_settings
    )

    def callback(adapter, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        try:
            tools.invoke_agent(
                mission_id, agent_id, turn_id, "baobao", "不该发生", provider_id
            )
        except MCPToolError:
            pass

    adapter = RecordingAdapter(callback)
    result = MissionRunner(repository, temp_settings, adapter).run(mission.id)

    assert result.status == MissionStatus.COMPLETED
    assert len(adapter.calls) == 4
    events = repository.list_events_after(mission.id, 0)
    assert not [e for e in events if e.event_type == "a2a.invoke"]
    assert "invoke_agent" not in adapter.calls[0]["prompt"]
