from backend.app.domain.models import (
    AgentProfile,
    CLIProvider,
    Mission,
    MissionStatus,
    RoomMode,
    TaskComplexity,
)


def test_mission_defaults_to_task_mode_and_pending_status():
    mission = Mission(id="m_1", title="Test", goal="Summarize the repo")

    assert mission.room_mode == RoomMode.TASK
    assert mission.status == MissionStatus.PENDING
    assert mission.complexity == TaskComplexity.SIMPLE


def test_room_mode_string_value_matches_str_enum_semantics():
    assert str(RoomMode.TASK) == "task"


def test_mission_timestamps_are_timezone_aware():
    mission = Mission(id="m_2", title="Time", goal="Track creation time")

    assert mission.created_at.tzinfo is not None
    assert mission.created_at.utcoffset() is not None


def test_cli_provider_args_defaults_are_isolated():
    first = CLIProvider(id="gemini", label="Gemini", command="gemini")
    second = CLIProvider(id="claude", label="Claude", command="claude")

    first.args.append("--debug")

    assert first.args == ["--debug"]
    assert second.args == []


def test_agent_profile_keeps_character_and_provider_separate():
    profile = AgentProfile(
        id="wangye",
        display_name="王也",
        role="分析 / 风险 / 复盘",
        persona="松弛但判断扎实",
        default_provider_id="gemini",
        fallback_provider_ids=["claude"],
        capability_hints=["risk"],
    )

    assert profile.id == "wangye"
    assert profile.default_provider_id == "gemini"
    assert profile.fallback_provider_ids == ["claude"]
