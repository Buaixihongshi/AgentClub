"""Evaluator-only behavioral contracts; injected after the solver has stopped."""
from backend.app.domain.models import Conversation, Message, MessageRole, Mission
from backend.app.mcp.tools import MCPTools
from backend.app.orchestration.context import build_conversation_context


def seed(repository):
    repository.create_conversation(Conversation(id="thread", title="discussion"))
    for mission_id in ("previous", "current"):
        repository.create_mission(Mission(id=mission_id, title=mission_id, goal="continue", conversation_id="thread"))
    repository.add_message(Message(id="before", mission_id="previous", conversation_id="thread", role=MessageRole.AGENT, content="初稿可行"))
    repository.add_event("previous", "interrupt", {"content": "补充约束：不得联网"})
    repository.add_message(Message(id="after", mission_id="current", conversation_id="thread", role=MessageRole.USER, content="请继续"))


def test_prior_interruption_survives_context_read(repository, temp_settings):
    seed(repository)
    tools = MCPTools(repository, temp_settings.artifact_root)
    first = tools.read_context("current")["messages"]
    second = tools.read_context("current")["messages"]
    assert [message["content"] for message in first] == ["初稿可行", "补充约束：不得联网", "请继续"]
    assert [message["id"] for message in first] == [message["id"] for message in second]
    assert len(repository.list_messages("previous")) == 1


def test_prompt_contains_prior_interruption(repository):
    seed(repository)
    context = build_conversation_context(repository, "thread")
    assert "补充约束：不得联网" in context
    assert context.index("初稿可行") < context.index("补充约束：不得联网") < context.index("请继续")


def test_regular_messages_remain_readable(repository, temp_settings):
    repository.create_mission(Mission(id="local", title="local", goal="local"))
    repository.add_message(Message(id="local-msg", mission_id="local", role=MessageRole.USER, content="普通请求"))
    messages = MCPTools(repository, temp_settings.artifact_root).read_context("local")["messages"]
    assert [message["content"] for message in messages] == ["普通请求"]


def test_other_conversation_does_not_leak(repository, temp_settings):
    seed(repository)
    repository.create_conversation(Conversation(id="other-thread", title="private"))
    repository.create_mission(Mission(id="other", title="other", goal="other", conversation_id="other-thread"))
    repository.add_message(Message(id="secret", mission_id="other", conversation_id="other-thread", role=MessageRole.USER, content="不可泄漏的其他会话"))
    context = MCPTools(repository, temp_settings.artifact_root).read_context("current")
    assert "不可泄漏的其他会话" not in str(context)
