"""Evaluator-only API and MCP contracts; does not prescribe a particular patch."""
import json

import pytest

from backend.app.domain.models import Conversation, Mission
from backend.app.mcp import server
from backend.app.mcp.tools import MCPToolError, MCPTools


def seed(repository, temp_settings, current_conversation="thread", source_conversation="thread"):
    for conversation_id in {current_conversation, source_conversation} - {None}:
        repository.create_conversation(Conversation(id=conversation_id, title=conversation_id))
    for mission_id, conversation_id in (("current", current_conversation), ("source", source_conversation)):
        repository.create_mission(Mission(id=mission_id, title=mission_id, goal="read file", conversation_id=conversation_id))
    tools = MCPTools(repository, temp_settings.artifact_root)
    tools.write_artifact("source", "chulan", "previous-turn", "plan.md", "交接计划正文")
    return tools


def test_same_conversation_source_can_be_read(repository, temp_settings):
    tools = seed(repository, temp_settings)
    result = tools.read_artifact("current", "baobao", "read-turn", "plan.md", source_mission_id="source", with_line_numbers=False)
    assert result["content"] == "交接计划正文"
    assert result["mission_id"] == "source"


def test_mcp_schema_and_dispatch_support_source(repository, temp_settings):
    tools = seed(repository, temp_settings)
    ctx = server._Context("current", "baobao", "read-turn", "codex", tools)
    descriptors = server._handle_tools_list(ctx, {})["tools"]
    descriptor = next(tool for tool in descriptors if tool["name"] == "read_artifact")
    assert "source_mission_id" in descriptor["inputSchema"]["properties"]
    result = server._handle_tools_call(ctx, {"name": "read_artifact", "arguments": {
        "relative_path": "plan.md", "source_mission_id": "source", "with_line_numbers": False,
    }})
    assert not result.get("isError")
    assert json.loads(result["content"][0]["text"])["content"] == "交接计划正文"


def test_other_conversation_source_is_rejected(repository, temp_settings):
    tools = seed(repository, temp_settings, source_conversation="other")
    with pytest.raises(MCPToolError):
        tools.read_artifact("current", "baobao", "read-turn", "plan.md", source_mission_id="source")


def test_missing_conversation_source_is_rejected(repository, temp_settings):
    tools = seed(repository, temp_settings, current_conversation=None, source_conversation=None)
    with pytest.raises(MCPToolError):
        tools.read_artifact("current", "baobao", "read-turn", "plan.md", source_mission_id="source")


def test_default_current_mission_read(repository, temp_settings):
    tools = seed(repository, temp_settings)
    tools.write_artifact("current", "baobao", "current-turn", "plan.md", "本任务正文")
    result = tools.read_artifact("current", "baobao", "read-turn", "plan.md", with_line_numbers=False)
    assert result["content"] == "本任务正文"


def test_local_path_escape_is_rejected(repository, temp_settings):
    tools = seed(repository, temp_settings)
    with pytest.raises(MCPToolError):
        tools.read_artifact("current", "baobao", "read-turn", "../../outside.txt")
