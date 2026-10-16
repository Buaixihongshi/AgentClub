from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.domain.models import Conversation, Message, MessageRole, Mission
from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.orchestration.speaker import WorkflowStep


def test_publish_message_requires_valid_mission(repository, temp_settings):
    tools = MCPTools(repository, temp_settings.artifact_root)

    with pytest.raises(MCPToolError, match="Unknown mission"):
        tools.publish_message("missing", "chulan", "t_1", "hello", "fake")


def test_publish_message_writes_chat_message(repository, temp_settings):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    message = tools.publish_message("m_1", "chulan", "t_1", "hello", "fake")

    assert message.agent_id == "chulan"
    assert repository.list_messages("m_1")[0].content == "hello"


def test_log_thought_records_agent_thought_event_only(repository, temp_settings):
    repository.create_mission(Mission(id="m_th1", title="Plan", goal="think"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    result = tools.log_thought(
        "m_th1",
        "baobao",
        "turn_1",
        "先做最小可跑版本。",
        "codex",
        kind="plan",
        tags=["backend", "mcp"],
    )

    assert result["ok"] is True
    assert isinstance(result["event_id"], str)
    assert repository.list_messages("m_th1") == []
    events = repository.list_events_after("m_th1", 0)
    thought_events = [event for event in events if event.event_type == "agent.thought"]
    assert len(thought_events) == 1
    payload = thought_events[0].payload
    assert payload["agent_id"] == "baobao"
    assert payload["turn_id"] == "turn_1"
    assert payload["provider_id"] == "codex"
    assert payload["kind"] == "plan"
    assert payload["content"] == "先做最小可跑版本。"
    assert payload["tags"] == ["backend", "mcp"]


def test_log_thought_defaults_missing_turn_id(repository, temp_settings):
    repository.create_mission(Mission(id="m_th2", title="Plan", goal="think"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.log_thought(
        "m_th2",
        "baobao",
        None,
        "turn 没给，先记 unknown。",
        "codex",
    )

    events = repository.list_events_after("m_th2", 0)
    thought_events = [event for event in events if event.event_type == "agent.thought"]
    assert len(thought_events) == 1
    assert thought_events[0].payload["turn_id"] == "unknown"
    assert thought_events[0].payload["kind"] == "reasoning"
    assert repository.list_messages("m_th2") == []


def test_read_context_returns_latest_messages_by_committed_event_order(repository, temp_settings):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Plan something"))
    repository.add_message(
        Message(
            id="msg_a",
            mission_id="m_1",
            role=MessageRole.AGENT,
            content="latest",
            created_at=datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc),
        )
    )
    repository.add_message(
        Message(
            id="msg_b",
            mission_id="m_1",
            role=MessageRole.AGENT,
            content="oldest",
            created_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
    )
    repository.add_message(
        Message(
            id="msg_c",
            mission_id="m_1",
            role=MessageRole.AGENT,
            content="middle",
            created_at=datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc),
        )
    )
    tools = MCPTools(repository, temp_settings.artifact_root)

    context = tools.read_context("m_1", limit=2)

    # Backdated timestamps do not reorder the committed dialogue timeline.
    assert [message["content"] for message in context["messages"]] == ["oldest", "middle"]


def test_read_context_spans_conversation_across_missions(repository, temp_settings):
    """`read_context` must surface messages from every mission in the
    same conversation, not just the current mission. The prompt header
    (built via build_conversation_context) is conversation-scoped; if
    the MCP tool stays mission-scoped, agents see a contradiction:
    the prompt cites prior turns but the tool returns nothing.
    """
    repository.create_conversation(Conversation(id="c_shared", title="thread"))
    repository.create_mission(
        Mission(id="m_prev", title="prev", goal="prev", conversation_id="c_shared")
    )
    repository.create_mission(
        Mission(id="m_curr", title="curr", goal="curr", conversation_id="c_shared")
    )
    repository.add_message(
        Message(
            id="msg_prev_user",
            mission_id="m_prev",
            conversation_id="c_shared",
            role=MessageRole.USER,
            content="楚岚先做规划",
            created_at=datetime(2026, 5, 21, 6, 57, tzinfo=timezone.utc),
        )
    )
    repository.add_message(
        Message(
            id="msg_prev_agent",
            mission_id="m_prev",
            conversation_id="c_shared",
            role=MessageRole.AGENT,
            agent_id="chulan",
            content="规划已落地：要点 1/2/3",
            created_at=datetime(2026, 5, 21, 6, 58, tzinfo=timezone.utc),
        )
    )
    repository.add_message(
        Message(
            id="msg_curr_user",
            mission_id="m_curr",
            conversation_id="c_shared",
            role=MessageRole.USER,
            content="@冯宝宝 楚岚规划好了，你来执行",
            created_at=datetime(2026, 5, 21, 7, 5, tzinfo=timezone.utc),
        )
    )
    tools = MCPTools(repository, temp_settings.artifact_root)

    context = tools.read_context("m_curr", limit=10)

    contents = [m["content"] for m in context["messages"]]
    assert contents == [
        "楚岚先做规划",
        "规划已落地：要点 1/2/3",
        "@冯宝宝 楚岚规划好了，你来执行",
    ], (
        "read_context on m_curr must include the prior mission's messages "
        "via conversation_id; got: {}".format(contents)
    )


def test_read_context_falls_back_to_mission_when_no_conversation(repository, temp_settings):
    """Missions created without a conversation_id (legacy / direct API
    use) must still work: read_context falls back to mission scope.
    """
    repository.create_mission(Mission(id="m_orphan", title="orphan", goal="orphan"))
    repository.add_message(
        Message(
            id="msg_orphan",
            mission_id="m_orphan",
            role=MessageRole.USER,
            content="孤立任务",
            created_at=datetime(2026, 5, 21, 8, 0, tzinfo=timezone.utc),
        )
    )
    tools = MCPTools(repository, temp_settings.artifact_root)

    context = tools.read_context("m_orphan", limit=10)

    assert [m["content"] for m in context["messages"]] == ["孤立任务"]


def test_write_artifact_rejects_path_traversal(repository, temp_settings):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    with pytest.raises(MCPToolError, match="outside artifact directory"):
        tools.write_artifact("m_1", "baobao", "t_1", "../escape.md", "bad")


def test_write_artifact_stays_under_mission_directory(repository, temp_settings):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    artifact_path = tools.write_artifact("m_1", "baobao", "t_1", "plan.md", "# Plan")

    assert artifact_path == temp_settings.artifact_root / "m_1" / "plan.md"
    assert artifact_path.read_text(encoding="utf-8") == "# Plan"


def test_write_artifact_records_frontend_metadata(repository, temp_settings):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.write_artifact("m_1", "baobao", "turn_1", "notes/result.md", "# result")

    artifact_events = [
        event
        for event in repository.list_events_after("m_1", 0)
        if event.event_type == "artifact"
    ]
    assert len(artifact_events) == 1
    payload = artifact_events[0].payload
    assert payload["path"] == "notes/result.md"
    assert not Path(payload["path"]).is_absolute()
    assert payload["agent_id"] == "baobao"
    assert payload["turn_id"] == "turn_1"
    assert payload["title"] == "result.md"


def test_write_artifact_records_normalized_event_path(repository, temp_settings):
    repository.create_mission(Mission(id="m_1", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    artifact_path = tools.write_artifact(
        "m_1", "baobao", "turn_1", "notes/../result.md", "# result"
    )

    assert artifact_path == temp_settings.artifact_root / "m_1" / "result.md"
    artifact_events = [
        event
        for event in repository.list_events_after("m_1", 0)
        if event.event_type == "artifact"
    ]
    assert len(artifact_events) == 1
    payload = artifact_events[0].payload
    assert payload["path"] == "result.md"
    assert payload["title"] == "result.md"


def test_read_artifact_returns_content_with_line_numbers(repository, temp_settings):
    repository.create_mission(Mission(id="m_ra1", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)
    tools.write_artifact("m_ra1", "baobao", "t_1", "plan.md", "line one\nline two\nline three")

    result = tools.read_artifact("m_ra1", "chulan", "t_2", "plan.md")

    assert result["path"] == "plan.md"
    assert result["truncated"] is False
    assert result["total_chars"] == len("line one\nline two\nline three")
    assert result["returned_chars"] == result["total_chars"]
    assert "    1\tline one" in result["content"]
    assert "    2\tline two" in result["content"]
    assert "    3\tline three" in result["content"]


def test_read_artifact_without_line_numbers_returns_raw(repository, temp_settings):
    repository.create_mission(Mission(id="m_ra2", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)
    tools.write_artifact("m_ra2", "baobao", "t_1", "plan.md", "hello\nworld")

    result = tools.read_artifact(
        "m_ra2", "chulan", "t_2", "plan.md", with_line_numbers=False
    )

    assert result["content"] == "hello\nworld"


def test_read_artifact_rejects_path_traversal(repository, temp_settings):
    repository.create_mission(Mission(id="m_ra3", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    with pytest.raises(MCPToolError, match="outside artifact directory"):
        tools.read_artifact("m_ra3", "chulan", "t_1", "../../etc/passwd")


def test_read_artifact_missing_file_raises(repository, temp_settings):
    repository.create_mission(Mission(id="m_ra4", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    with pytest.raises(MCPToolError, match="Artifact not found"):
        tools.read_artifact("m_ra4", "chulan", "t_1", "nope.md")


def test_read_artifact_truncates_when_too_large(repository, temp_settings):
    repository.create_mission(Mission(id="m_ra5", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)
    big = "x" * (MCPTools.READ_ARTIFACT_MAX_CHARS + 500)
    tools.write_artifact("m_ra5", "baobao", "t_1", "big.txt", big)

    result = tools.read_artifact(
        "m_ra5", "chulan", "t_2", "big.txt", with_line_numbers=False
    )

    assert result["truncated"] is True
    assert result["total_chars"] == len(big)
    assert result["returned_chars"] == MCPTools.READ_ARTIFACT_MAX_CHARS
    assert len(result["content"]) == MCPTools.READ_ARTIFACT_MAX_CHARS


def test_read_artifact_emits_tool_trace(repository, temp_settings):
    repository.create_mission(Mission(id="m_ra6", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)
    tools.write_artifact("m_ra6", "baobao", "t_1", "plan.md", "draft content")

    tools.read_artifact("m_ra6", "chulan", "t_2", "plan.md", provider_id="claude")

    events = repository.list_events_after("m_ra6", 0)
    calls = [e for e in events if e.event_type == "tool.call" and e.payload.get("tool_name") == "read_artifact"]
    results = [e for e in events if e.event_type == "tool.result" and e.payload.get("tool_name") == "read_artifact"]
    assert len(calls) == 1
    assert calls[0].payload["agent_id"] == "chulan"
    assert calls[0].payload["turn_id"] == "t_2"
    assert calls[0].payload["provider_id"] == "claude"
    assert len(results) == 1


def test_read_artifact_missing_file_emits_tool_error(repository, temp_settings):
    repository.create_mission(Mission(id="m_ra7", title="Plan", goal="Plan something"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    with pytest.raises(MCPToolError):
        tools.read_artifact("m_ra7", "chulan", "t_1", "missing.md")

    events = repository.list_events_after("m_ra7", 0)
    errors = [e for e in events if e.event_type == "tool.error" and e.payload.get("tool_name") == "read_artifact"]
    assert len(errors) == 1


def test_record_tool_call_writes_event(repository, temp_settings):
    from backend.app.orchestration.events import record_tool_call

    repository.create_mission(Mission(id="m_tc1", title="Plan", goal="test tool trace"))

    record_tool_call(
        repository,
        mission_id="m_tc1",
        turn_id="turn_1_abc",
        agent_id="baobao",
        tool_name="write_artifact",
        tool_input={"relative_path": "summary.md"},
        provider_id="codex",
    )

    events = repository.list_events_after("m_tc1", 0)
    tool_events = [e for e in events if e.event_type == "tool.call"]
    assert len(tool_events) == 1
    payload = tool_events[0].payload
    assert payload["tool_name"] == "write_artifact"
    assert payload["agent_id"] == "baobao"
    assert payload["turn_id"] == "turn_1_abc"
    assert payload["provider_id"] == "codex"
    assert payload["tool_input"] == {"relative_path": "summary.md"}


def test_record_tool_result_writes_event(repository, temp_settings):
    from backend.app.orchestration.events import record_tool_result

    repository.create_mission(Mission(id="m_tr1", title="Plan", goal="test tool trace"))

    record_tool_result(
        repository,
        mission_id="m_tr1",
        turn_id="turn_1_abc",
        agent_id="baobao",
        tool_name="write_artifact",
        summary="Wrote summary.md",
        artifact_id="m_tr1:summary.md",
    )

    events = repository.list_events_after("m_tr1", 0)
    tool_events = [e for e in events if e.event_type == "tool.result"]
    assert len(tool_events) == 1
    payload = tool_events[0].payload
    assert payload["summary"] == "Wrote summary.md"
    assert payload["artifact_id"] == "m_tr1:summary.md"


def test_record_tool_error_writes_event(repository, temp_settings):
    from backend.app.orchestration.events import record_tool_error

    repository.create_mission(Mission(id="m_te1", title="Plan", goal="test tool trace"))

    record_tool_error(
        repository,
        mission_id="m_te1",
        turn_id="turn_1_abc",
        agent_id="baobao",
        tool_name="write_artifact",
        error="Artifact path is outside artifact directory",
    )

    events = repository.list_events_after("m_te1", 0)
    tool_events = [e for e in events if e.event_type == "tool.error"]
    assert len(tool_events) == 1
    assert tool_events[0].payload["error"] == "Artifact path is outside artifact directory"


def test_tool_call_sanitizes_write_artifact_content(repository, temp_settings):
    from backend.app.orchestration.events import record_tool_call

    repository.create_mission(Mission(id="m_ts1", title="Plan", goal="test sanitize"))

    record_tool_call(
        repository,
        mission_id="m_ts1",
        turn_id="turn_1_abc",
        agent_id="baobao",
        tool_name="write_artifact",
        tool_input={"relative_path": "summary.md", "content": "# Very long content..." + "x" * 500},
    )

    events = repository.list_events_after("m_ts1", 0)
    tool_events = [e for e in events if e.event_type == "tool.call"]
    payload = tool_events[0].payload
    assert "content" not in payload["tool_input"]
    assert payload["tool_input"]["relative_path"] == "summary.md"


def test_tool_call_truncates_long_input_values(repository, temp_settings):
    from backend.app.orchestration.events import record_tool_call

    repository.create_mission(Mission(id="m_tl1", title="Plan", goal="test truncate"))

    record_tool_call(
        repository,
        mission_id="m_tl1",
        turn_id="turn_1_abc",
        agent_id="baobao",
        tool_name="read_context",
        tool_input={"query": "x" * 300},
    )

    events = repository.list_events_after("m_tl1", 0)
    payload = events[-1].payload
    assert len(payload["tool_input"]["query"]) < 300
    assert payload["tool_input"]["query"].endswith("...[truncated]")


def test_publish_message_emits_tool_trace(repository, temp_settings):
    repository.create_mission(Mission(id="m_pt1", title="Plan", goal="test trace"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.publish_message("m_pt1", "chulan", "t_1", "hello", "fake")

    events = repository.list_events_after("m_pt1", 0)
    tool_calls = [e for e in events if e.event_type == "tool.call"]
    tool_results = [e for e in events if e.event_type == "tool.result"]
    assert len(tool_calls) == 1
    assert tool_calls[0].payload["tool_name"] == "publish_message"
    assert len(tool_results) == 1
    assert "Published message" in tool_results[0].payload["summary"]


def test_publish_message_trace_can_be_disabled(repository, temp_settings):
    repository.create_mission(Mission(id="m_pt2", title="Plan", goal="test no trace"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.publish_message("m_pt2", "chulan", "t_1", "hello", "fake", trace=False)

    events = repository.list_events_after("m_pt2", 0)
    tool_calls = [e for e in events if e.event_type == "tool.call"]
    assert len(tool_calls) == 0


def test_write_artifact_emits_tool_trace(repository, temp_settings):
    repository.create_mission(Mission(id="m_wa1", title="Plan", goal="test trace"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.write_artifact("m_wa1", "baobao", "t_1", "plan.md", "# Plan")

    events = repository.list_events_after("m_wa1", 0)
    tool_calls = [e for e in events if e.event_type == "tool.call"]
    tool_results = [e for e in events if e.event_type == "tool.result"]
    assert len(tool_calls) == 1
    assert tool_calls[0].payload["tool_name"] == "write_artifact"
    assert tool_calls[0].payload["tool_input"]["relative_path"] == "plan.md"
    assert "content" not in tool_calls[0].payload["tool_input"]
    assert len(tool_results) == 1
    assert tool_results[0].payload["artifact_id"] == "m_wa1:plan.md"


def test_write_artifact_path_traversal_emits_tool_error(repository, temp_settings):
    repository.create_mission(Mission(id="m_wa2", title="Plan", goal="test error"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    with pytest.raises(MCPToolError):
        tools.write_artifact("m_wa2", "baobao", "t_1", "../escape.md", "bad")

    events = repository.list_events_after("m_wa2", 0)
    tool_errors = [e for e in events if e.event_type == "tool.error"]
    assert len(tool_errors) == 1
    assert "outside artifact directory" in tool_errors[0].payload["error"]


def test_read_context_emits_tool_trace_when_agent_context_provided(repository, temp_settings):
    repository.create_mission(Mission(id="m_rc1", title="Plan", goal="test trace"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.read_context("m_rc1", agent_id="chulan", turn_id="t_1", provider_id="fake")

    events = repository.list_events_after("m_rc1", 0)
    tool_calls = [e for e in events if e.event_type == "tool.call"]
    tool_results = [e for e in events if e.event_type == "tool.result"]
    assert len(tool_calls) == 1
    assert tool_calls[0].payload["tool_name"] == "read_context"
    assert len(tool_results) == 1


def test_read_context_skips_trace_without_agent_context(repository, temp_settings):
    repository.create_mission(Mission(id="m_rc2", title="Plan", goal="test no trace"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.read_context("m_rc2")

    events = repository.list_events_after("m_rc2", 0)
    tool_calls = [e for e in events if e.event_type == "tool.call"]
    assert len(tool_calls) == 0


def test_update_status_emits_trace_with_turn_context(repository, temp_settings):
    repository.create_mission(Mission(id="m_us1", title="Plan", goal="test trace"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.update_status("m_us1", "running", summary="started", agent_id="chulan", turn_id="t_1")

    events = repository.list_events_after("m_us1", 0)
    tool_calls = [e for e in events if e.event_type == "tool.call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].payload["tool_name"] == "update_status"


def test_update_status_skips_trace_without_turn_context(repository, temp_settings):
    repository.create_mission(Mission(id="m_us2", title="Plan", goal="test no trace"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    tools.update_status("m_us2", "running", summary="started")

    events = repository.list_events_after("m_us2", 0)
    tool_calls = [e for e in events if e.event_type == "tool.call"]
    assert len(tool_calls) == 0


def test_publish_message_attaches_conversation_id(repository, temp_settings):
    repository.create_conversation(Conversation(id="c_pm", title="test"))
    repository.create_mission(Mission(
        id="m_pm", title="Plan", goal="test", conversation_id="c_pm",
    ))
    tools = MCPTools(repository, temp_settings.artifact_root)

    message = tools.publish_message("m_pm", "chulan", "t_1", "hello", "fake")

    assert message.conversation_id == "c_pm"
    conv_messages = repository.list_messages_for_conversation("c_pm")
    assert any(m.id == message.id for m in conv_messages)


def test_publish_message_without_conversation_leaves_none(repository, temp_settings):
    repository.create_mission(Mission(id="m_pm2", title="Plan", goal="test"))
    tools = MCPTools(repository, temp_settings.artifact_root)

    message = tools.publish_message("m_pm2", "chulan", "t_1", "hello", "fake")

    assert message.conversation_id is None


def test_invoke_agent_appends_workflow_step_and_records_event(repository, temp_settings):
    repository.create_mission(Mission(id="m_a2a_tool1", title="Plan", goal="Plan something"))
    queued: list[WorkflowStep] = []
    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=queued.append,
        valid_agent_ids={"chulan", "baobao", "wangye"},
        max_a2a_invocations=8,
        a2a_budget_used=lambda: 0,
    )

    result = tools.invoke_agent(
        "m_a2a_tool1",
        "chulan",
        "turn_1_sender",
        "baobao",
        "核对异闻物档案",
        "codex",
    )

    assert result == {"queued_position": 1, "estimated_remaining_a2a_budget": 7}
    assert len(queued) == 1
    step = queued[0]
    assert step.agent_id == "baobao"
    assert step.purpose == "核对异闻物档案"
    assert step.origin == "a2a"
    assert step.sender_agent_id == "chulan"
    assert step.sender_turn_id == "turn_1_sender"
    assert step.parent_step_id is None
    assert len(step.step_id) == 8
    events = repository.list_events_after("m_a2a_tool1", 0)
    assert [event.event_type for event in events if event.event_type == "a2a.invoke"] == ["a2a.invoke"]
    assert any(event.event_type == "tool.call" and event.payload["tool_name"] == "invoke_agent" for event in events)
    assert any(event.event_type == "tool.result" and event.payload["tool_name"] == "invoke_agent" for event in events)


def test_invoke_agent_rejects_unknown_target(repository, temp_settings):
    repository.create_mission(Mission(id="m_a2a_tool2", title="Plan", goal="Plan something"))
    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=lambda step: None,
        valid_agent_ids={"chulan", "baobao"},
        max_a2a_invocations=8,
        a2a_budget_used=lambda: 0,
    )

    with pytest.raises(MCPToolError, match="unknown agent: luyi"):
        tools.invoke_agent("m_a2a_tool2", "chulan", "turn_1", "luyi", "找人", "codex")


def test_invoke_agent_rejects_self_invoke(repository, temp_settings):
    repository.create_mission(Mission(id="m_a2a_tool3", title="Plan", goal="Plan something"))
    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=lambda step: None,
        valid_agent_ids={"chulan", "baobao"},
        max_a2a_invocations=8,
        a2a_budget_used=lambda: 0,
    )

    with pytest.raises(MCPToolError, match="agent cannot invoke itself"):
        tools.invoke_agent("m_a2a_tool3", "chulan", "turn_1", "chulan", "自己再来", "codex")


def test_invoke_agent_wait_reply_returns_reply_pending(repository, temp_settings):
    repository.create_mission(Mission(id="m_a2a_tool4", title="Plan", goal="Plan something"))
    queued: list[WorkflowStep] = []
    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=queued.append,
        valid_agent_ids={"chulan", "baobao"},
        max_a2a_invocations=8,
        a2a_budget_used=lambda: 0,
    )

    result = tools.invoke_agent(
        "m_a2a_tool4",
        "chulan",
        "turn_1_sender",
        "baobao",
        "等回复",
        "codex",
        mode="wait_reply",
    )

    assert result["queued_position"] == 1
    assert result["reply_pending"] is True
    assert result["estimated_remaining_a2a_budget"] == 7
    assert "resume_position" in result
    assert len(queued) == 2
    assert queued[0].origin == "a2a"
    assert queued[0].agent_id == "baobao"
    assert queued[1].origin == "a2a_resume"
    assert queued[1].agent_id == "chulan"
    assert queued[1].parent_step_id == queued[0].step_id

    events = repository.list_events_after("m_a2a_tool4", 0)
    invokes = [e for e in events if e.event_type == "a2a.invoke"]
    assert len(invokes) == 1
    assert invokes[0].payload["mode"] == "wait_reply"


def test_invoke_agent_rejects_unknown_mode(repository, temp_settings):
    repository.create_mission(Mission(id="m_a2a_mode_bad", title="Plan", goal="g"))
    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=lambda step: None,
        valid_agent_ids={"chulan", "baobao"},
        max_a2a_invocations=8,
        a2a_budget_used=lambda: 0,
    )

    with pytest.raises(MCPToolError, match="mode bogus_mode not supported"):
        tools.invoke_agent(
            "m_a2a_mode_bad",
            "chulan",
            "turn_1",
            "baobao",
            "x",
            "codex",
            mode="bogus_mode",
        )


def test_invoke_agent_rejects_exhausted_budget(repository, temp_settings):
    repository.create_mission(Mission(id="m_a2a_tool5", title="Plan", goal="Plan something"))
    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=lambda step: None,
        valid_agent_ids={"chulan", "baobao"},
        max_a2a_invocations=1,
        a2a_budget_used=lambda: 1,
    )

    with pytest.raises(MCPToolError, match="a2a 深度耗尽"):
        tools.invoke_agent("m_a2a_tool5", "chulan", "turn_1", "baobao", "继续", "codex")


def test_invoke_agent_subprocess_remaining_budget_accounts_for_prior_events(repository, temp_settings):
    """Remaining budget in subprocess path must count all mission-level a2a events,
    not just the current sender turn's requests."""
    from backend.app.orchestration.events import record_a2a_invoke

    repository.create_mission(Mission(id="m_a2a_budget2", title="Plan", goal="go"))

    # Simulate a prior invoke that was already executed (a2a.invoke event in DB).
    record_a2a_invoke(
        repository, "m_a2a_budget2", "baobao", "turn_prior", "wangye", "先前任务", "fire_and_forget", 1
    )

    # Subprocess path (a2a_enqueue=None), max=3, prior a2a.invoke count=1.
    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=None,
        valid_agent_ids={"chulan", "baobao", "wangye"},
        max_a2a_invocations=3,
    )

    result = tools.invoke_agent("m_a2a_budget2", "chulan", "turn_2", "baobao", "补充调查", "codex")

    # After this call: 1 prior a2a.invoke + 1 new a2a.request = 2 total; remaining = 3 - 2 = 1
    assert result["estimated_remaining_a2a_budget"] == 1, (
        "remaining budget must reflect all mission-level a2a events, got: {r}".format(
            r=result["estimated_remaining_a2a_budget"]
        )
    )


def test_invoke_agent_wait_reply_rejects_cycle(repository, temp_settings):
    """A wait_reply that would re-invoke an agent already in the resume chain
    must raise — otherwise the chain deadlocks (A waits B, B waits A)."""
    repository.create_mission(Mission(id="m_cycle1", title="Plan", goal="g"))

    def peek_resume_chain(sender_agent_id: str):
        if sender_agent_id == "baobao":
            return ["chulan"]
        return []

    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=lambda step: 1,
        valid_agent_ids={"chulan", "baobao"},
        max_a2a_invocations=8,
        a2a_budget_used=lambda: 0,
        peek_resume_chain=peek_resume_chain,
    )

    with pytest.raises(MCPToolError, match="a2a wait cycle"):
        tools.invoke_agent(
            "m_cycle1", "baobao", "turn_b1", "chulan", "回叫", "codex",
            mode="wait_reply",
        )


def test_invoke_agent_wait_reply_no_cycle_when_target_not_in_chain(repository, temp_settings):
    """fire_and_forget mode or target not in chain should not trigger cycle error."""
    repository.create_mission(Mission(id="m_cycle2", title="Plan", goal="g"))

    def peek_resume_chain(sender_agent_id: str):
        if sender_agent_id == "chulan":
            return ["wangye"]
        return []

    queued = []
    tools = MCPTools(
        repository,
        temp_settings.artifact_root,
        a2a_enqueue=queued.append,
        valid_agent_ids={"chulan", "baobao", "wangye"},
        max_a2a_invocations=8,
        a2a_budget_used=lambda: 0,
        peek_resume_chain=peek_resume_chain,
    )

    result = tools.invoke_agent(
        "m_cycle2", "chulan", "turn_c1", "baobao", "ok", "codex",
        mode="wait_reply",
    )
    assert result["reply_pending"] is True


@pytest.mark.parametrize("current_conv,source_conv,allowed", [
    ("shared", "shared", True), ("shared", "other", False),
    (None, "shared", False), ("shared", None, False), (None, None, False),
])
def test_read_artifact_cross_mission_requires_same_nonempty_conversation(
    repository, temp_settings, current_conv, source_conv, allowed
):
    for cid in {current_conv, source_conv} - {None}:
        repository.create_conversation(Conversation(id=cid, title=cid))
    for mid, cid in (("current", current_conv), ("source", source_conv)):
        repository.create_mission(Mission(id=mid, title=mid, goal="g", conversation_id=cid))
    tools = MCPTools(repository, temp_settings.artifact_root)
    tools.write_artifact("source", "chulan", "t1", "spec.md", "source spec")
    if allowed:
        result = tools.read_artifact("current", "baobao", "t2", "spec.md", source_mission_id="source")
        assert result["mission_id"] == "source"
        assert "source spec" in result["content"]
    else:
        with pytest.raises(MCPToolError, match="different conversation"):
            tools.read_artifact("current", "baobao", "t2", "spec.md", source_mission_id="source")


@pytest.mark.parametrize("path", ["../outside.txt", "link.txt"])
def test_cross_mission_read_still_rejects_path_and_symlink_escape(repository, temp_settings, path):
    repository.create_conversation(Conversation(id="shared", title="shared"))
    for mid in ("source", "current"):
        repository.create_mission(Mission(id=mid, title=mid, goal="g", conversation_id="shared"))
    tools = MCPTools(repository, temp_settings.artifact_root)
    tools.write_artifact("source", "chulan", "t1", "spec.md", "spec")
    outside = temp_settings.artifact_root / "outside.txt"
    outside.write_text("private")
    (temp_settings.artifact_root / "source/link.txt").symlink_to(outside)
    with pytest.raises(MCPToolError, match="outside artifact directory"):
        tools.read_artifact("current", "baobao", "t2", path, source_mission_id="source")
