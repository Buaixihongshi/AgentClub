from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.domain.models import (
    Conversation,
    Message,
    MessageRole,
    Mission,
    MissionStatus,
    TurnStatus,
)
from backend.app.mcp.tools import MCPTools
from backend.app.orchestration.context import build_artifact_index, build_conversation_context
from backend.app.orchestration.runner import MissionRunner


def test_none_conversation_id_returns_empty(repository):
    assert build_conversation_context(repository, None) == ""


def test_empty_conversation_id_returns_empty(repository):
    assert build_conversation_context(repository, "") == ""


def test_no_messages_returns_empty(repository):
    repository.create_conversation(Conversation(id="c_empty", title="空"))
    assert build_conversation_context(repository, "c_empty") == ""


def test_formats_user_and_agent_messages(repository):
    repository.create_conversation(Conversation(id="c_1", title="测试"))
    repository.create_mission(Mission(id="m_1", title="t", goal="g", conversation_id="c_1"))
    repository.add_message(Message(
        id="msg_1", mission_id="m_1", conversation_id="c_1",
        role=MessageRole.USER, content="帮我分析一下",
    ))
    repository.add_message(Message(
        id="msg_2", mission_id="m_1", conversation_id="c_1",
        role=MessageRole.AGENT, content="风险在于并发", agent_id="wangye",
    ))
    repository.add_message(Message(
        id="msg_3", mission_id="m_1", conversation_id="c_1",
        role=MessageRole.AGENT, content="我来拆解任务", agent_id="chulan",
    ))

    ctx = build_conversation_context(repository, "c_1")

    assert ctx.startswith("最近对话：\n")
    assert "用户：帮我分析一下" in ctx
    assert "王也：风险在于并发" in ctx
    assert "张楚岚：我来拆解任务" in ctx


def test_long_message_truncated(repository):
    repository.create_conversation(Conversation(id="c_long", title="长"))
    repository.create_mission(Mission(id="m_l", title="t", goal="g", conversation_id="c_long"))
    long_content = "x" * 500
    repository.add_message(Message(
        id="msg_long", mission_id="m_l", conversation_id="c_long",
        role=MessageRole.USER, content=long_content,
    ))

    ctx = build_conversation_context(repository, "c_long", max_chars_per_message=50)

    lines = ctx.split("\n")
    user_line = [l for l in lines if l.startswith("用户：")][0]
    assert "已截断" in user_line
    assert "id=msg_long" in user_line
    assert len(user_line) < 100


def test_respects_limit(repository):
    repository.create_conversation(Conversation(id="c_lim", title="限"))
    repository.create_mission(Mission(id="m_lim", title="t", goal="g", conversation_id="c_lim"))
    for i in range(10):
        repository.add_message(Message(
            id="msg_{i}".format(i=i), mission_id="m_lim", conversation_id="c_lim",
            role=MessageRole.USER, content="消息{i}".format(i=i),
        ))

    ctx = build_conversation_context(repository, "c_lim", limit=3)

    lines = [l for l in ctx.split("\n") if l.startswith("用户：")]
    assert len(lines) == 3
    assert "消息7" in ctx
    assert "消息8" in ctx
    assert "消息9" in ctx
    assert "消息0" not in ctx


def test_system_role_excluded_from_dialogue(repository):
    repository.create_conversation(Conversation(id="c_sys", title="sys"))
    repository.create_mission(Mission(id="m_sys", title="t", goal="g", conversation_id="c_sys"))
    repository.add_message(Message(
        id="msg_sys", mission_id="m_sys", conversation_id="c_sys",
        role=MessageRole.SYSTEM, content="任务已完成",
    ))

    ctx = build_conversation_context(repository, "c_sys")

    assert ctx == ""


def test_unknown_agent_id_uses_raw_id(repository):
    repository.create_conversation(Conversation(id="c_unk", title="unk"))
    repository.create_mission(Mission(id="m_unk", title="t", goal="g", conversation_id="c_unk"))
    repository.add_message(Message(
        id="msg_unk", mission_id="m_unk", conversation_id="c_unk",
        role=MessageRole.AGENT, content="你好", agent_id="luyi",
    ))

    ctx = build_conversation_context(repository, "c_unk")

    assert "luyi：你好" in ctx


class PromptCapturingAdapter:
    def __init__(self):
        self.prompts = []

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        self.prompts.append(prompt)
        tools.publish_message(mission_id, agent_id, turn_id, "ok", provider_id)
        return TurnOutcome(status=TurnStatus.COMPLETED)


def test_second_mission_prompt_includes_first_mission_agent_messages(
    repository, temp_settings,
):
    repository.create_conversation(Conversation(id="c_e2e", title="e2e"))
    repository.create_mission(Mission(
        id="m_first", title="first", goal="快速任务",
        conversation_id="c_e2e",
    ))
    repository.add_message(Message(
        id="msg_user1", mission_id="m_first", conversation_id="c_e2e",
        role=MessageRole.USER, content="帮我做规划",
    ))

    runner1 = MissionRunner(repository, temp_settings, adapter=FakeCLIAdapter())
    temp_settings.max_turns = 1
    runner1.run("m_first")

    agent_msgs = [
        m for m in repository.list_messages_for_conversation("c_e2e")
        if m.role == MessageRole.AGENT
    ]
    assert agent_msgs, "FakeCLIAdapter should have published an agent message with conversation_id"

    repository.create_mission(Mission(
        id="m_second", title="second", goal="继续实现",
        conversation_id="c_e2e",
    ))

    capture = PromptCapturingAdapter()
    runner2 = MissionRunner(repository, temp_settings, adapter=capture)
    runner2.run("m_second")

    prompt = capture.prompts[0]
    assert "最近对话" in prompt
    assert "用户：帮我做规划" in prompt
    assert any(
        m.content[:40] in prompt for m in agent_msgs
    ), "Prompt should contain (possibly truncated) agent message from first mission"


def test_artifact_index_empty_when_no_artifacts(repository, temp_settings):
    repository.create_mission(Mission(id="m_ai0", title="t", goal="g"))
    assert build_artifact_index(repository, "m_ai0") == ""


def test_artifact_index_lists_paths_and_authors(repository, temp_settings):
    repository.create_mission(Mission(id="m_ai1", title="t", goal="g"))
    tools = MCPTools(repository, temp_settings.artifact_root)
    tools.write_artifact("m_ai1", "baobao", "t_1", "plan.md", "# Plan")
    tools.write_artifact("m_ai1", "wangye", "t_2", "notes/risk.md", "风险")

    index = build_artifact_index(repository, "m_ai1")

    assert "产物清单" in index
    assert "plan.md" in index
    assert "notes/risk.md" in index
    assert "baobao" in index
    assert "wangye" in index


def test_artifact_index_dedupes_paths_keeping_latest_writer(repository, temp_settings):
    repository.create_mission(Mission(id="m_ai2", title="t", goal="g"))
    tools = MCPTools(repository, temp_settings.artifact_root)
    tools.write_artifact("m_ai2", "baobao", "t_1", "plan.md", "v1")
    tools.write_artifact("m_ai2", "chulan", "t_2", "plan.md", "v2")

    index = build_artifact_index(repository, "m_ai2")

    listed = [line for line in index.splitlines() if line.startswith("- ")]
    assert len(listed) == 1
    assert "chulan" in listed[0]
    assert "baobao" not in listed[0]


def test_artifact_index_respects_limit(repository, temp_settings):
    repository.create_mission(Mission(id="m_ai3", title="t", goal="g"))
    tools = MCPTools(repository, temp_settings.artifact_root)
    for i in range(5):
        tools.write_artifact("m_ai3", "baobao", "t_{i}".format(i=i), "f{i}.md".format(i=i), "x")

    index = build_artifact_index(repository, "m_ai3", limit=3)

    listed = [line for line in index.splitlines() if line.startswith("- ")]
    assert len(listed) == 3
