from backend.app.adapters.stream_events import StreamEventType
from backend.app.adapters.stream_parsers import parse_stream_line
from backend.app.adapters.stream_parsers.claude import ClaudeStreamParser


def test_gemini_init_emits_session_started():
    line = '{"type":"init","session_id":"s_abc"}'
    ev = parse_stream_line("gemini", line)
    assert ev is not None
    assert ev.type is StreamEventType.SESSION_STARTED
    assert ev.session_id == "s_abc"


def test_gemini_assistant_message_emits_partial_text():
    line = '{"type":"message","role":"assistant","content":"摸鱼"}'
    ev = parse_stream_line("gemini", line)
    assert ev is not None
    assert ev.type is StreamEventType.PARTIAL_TEXT
    assert ev.text == "摸鱼"


def test_gemini_user_message_returns_none():
    line = '{"type":"message","role":"user","content":"问题"}'
    assert parse_stream_line("gemini", line) is None


def test_gemini_tool_use_emits_tool_attempt():
    line = '{"type":"tool_use","tool_name":"publish_message"}'
    ev = parse_stream_line("gemini", line)
    assert ev is not None
    assert ev.type is StreamEventType.TOOL_ATTEMPT
    assert ev.tool_name == "publish_message"


def test_gemini_result_success_emits_finished():
    line = '{"type":"result","status":"success"}'
    ev = parse_stream_line("gemini", line)
    assert ev is not None
    assert ev.type is StreamEventType.FINISHED


def test_gemini_result_error_emits_error():
    line = '{"type":"result","status":"failed","error":"boom"}'
    ev = parse_stream_line("gemini", line)
    assert ev is not None
    assert ev.type is StreamEventType.ERROR
    assert ev.error == "boom"


def test_gemini_garbage_returns_none():
    assert parse_stream_line("gemini", "not json") is None
    assert parse_stream_line("gemini", "{}") is None
    assert parse_stream_line("gemini", "") is None


def test_unknown_provider_returns_none():
    assert parse_stream_line("unknown", '{"type":"init"}') is None


def test_codex_thread_started_emits_session_started():
    line = '{"type":"thread.started","thread_id":"th_x"}'
    ev = parse_stream_line("codex", line)
    assert ev is not None
    assert ev.type is StreamEventType.SESSION_STARTED
    assert ev.session_id == "th_x"


def test_codex_turn_started_returns_none():
    # turn.started carries no payload we surface
    assert parse_stream_line("codex", '{"type":"turn.started"}') is None


def test_codex_item_completed_agent_message_emits_partial_text():
    line = '{"type":"item.completed","item":{"id":"i_0","type":"agent_message","text":"摸鱼是一门艺术"}}'
    ev = parse_stream_line("codex", line)
    assert ev is not None
    assert ev.type is StreamEventType.PARTIAL_TEXT
    assert ev.text == "摸鱼是一门艺术"


def test_codex_item_completed_non_agent_message_returns_none():
    # other item types (e.g. reasoning, tool calls) are ignored by partial text
    line = '{"type":"item.completed","item":{"id":"i_1","type":"reasoning","text":"thinking..."}}'
    assert parse_stream_line("codex", line) is None


def test_codex_tool_item_emits_tool_attempt():
    line = '{"type":"item.started","item":{"id":"i_2","type":"mcp_tool_call","name":"read_context"}}'
    ev = parse_stream_line("codex", line)
    assert ev is not None
    assert ev.type is StreamEventType.TOOL_ATTEMPT
    assert ev.tool_name == "read_context"


def test_codex_turn_completed_emits_finished():
    line = '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}'
    ev = parse_stream_line("codex", line)
    assert ev is not None
    assert ev.type is StreamEventType.FINISHED


def test_codex_garbage_returns_none():
    assert parse_stream_line("codex", "Reading additional input from stdin...") is None
    assert parse_stream_line("codex", "{}") is None


def test_claude_system_init_emits_session_started():
    line = '{"type":"system","subtype":"init","session_id":"s_abc","model":"claude-opus-4-7"}'
    ev = parse_stream_line("claude", line)
    assert ev is not None
    assert ev.type is StreamEventType.SESSION_STARTED
    assert ev.session_id == "s_abc"


def test_claude_system_hook_started_returns_none():
    line = '{"type":"system","subtype":"hook_started","hook_id":"h"}'
    assert parse_stream_line("claude", line) is None


def test_claude_system_hook_response_returns_none():
    line = '{"type":"system","subtype":"hook_response","hook_id":"h","output":"big blob"}'
    assert parse_stream_line("claude", line) is None


def test_claude_system_status_returns_none():
    assert parse_stream_line("claude", '{"type":"system","subtype":"status","status":"requesting"}') is None


def test_claude_content_block_delta_emits_partial_text():
    line = '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"我"}}}'
    ev = parse_stream_line("claude", line)
    assert ev is not None
    assert ev.type is StreamEventType.PARTIAL_TEXT
    assert ev.text == "我"


def test_claude_publish_message_input_json_delta_emits_incremental_content():
    parser = ClaudeStreamParser()
    lines = [
        '{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","name":"mcp__yiren_hub__publish_message","input":{}}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"content\\": \\"流式探针"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"继续冒"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"出来\\"}"}}}',
    ]

    events = [parser.parse(line) for line in lines]
    texts = [ev.text for ev in events if ev is not None and ev.type is StreamEventType.PARTIAL_TEXT]

    assert texts == ["流式探针", "继续冒", "出来"]


def test_claude_ignores_input_json_delta_for_non_publish_message_tools():
    parser = ClaudeStreamParser()
    lines = [
        '{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","name":"ToolSearch","input":{}}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"content\\": \\"不要显示\\"}"}}}',
    ]

    events = [parser.parse(line) for line in lines]
    assert events[0] is not None
    assert events[0].type is StreamEventType.TOOL_ATTEMPT
    assert events[0].tool_name == "ToolSearch"
    assert events[1] is None


def test_claude_message_start_returns_none():
    assert parse_stream_line("claude", '{"type":"stream_event","event":{"type":"message_start","message":{}}}') is None


def test_claude_message_stop_returns_none():
    assert parse_stream_line("claude", '{"type":"stream_event","event":{"type":"message_stop"}}') is None


def test_claude_assistant_assembled_returns_none():
    # The assembled assistant message is not authoritative for our pipeline
    line = '{"type":"assistant","message":{"id":"m","content":[{"type":"text","text":"x"}]}}'
    assert parse_stream_line("claude", line) is None


def test_claude_rate_limit_event_returns_none():
    assert parse_stream_line("claude", '{"type":"rate_limit_event","rate_limit_info":{}}') is None


def test_claude_result_success_emits_finished():
    line = '{"type":"result","subtype":"success","is_error":false,"result":"hi"}'
    ev = parse_stream_line("claude", line)
    assert ev is not None
    assert ev.type is StreamEventType.FINISHED


def test_claude_result_error_emits_error():
    line = '{"type":"result","subtype":"error","is_error":true,"result":"boom"}'
    ev = parse_stream_line("claude", line)
    assert ev is not None
    assert ev.type is StreamEventType.ERROR
    assert ev.error == "boom"


def test_codex_tool_item_carries_activity_id_and_phase():
    """The id is what lets provider.activity record a native tool at all."""
    started = parse_stream_line(
        "codex", '{"type":"item.started","item":{"id":"i_2","type":"command_execution","command":"pytest"}}')
    finished = parse_stream_line(
        "codex", '{"type":"item.completed","item":{"id":"i_2","type":"command_execution","command":"pytest"}}')

    assert started is not None and finished is not None
    assert started.activity_id == "i_2" and started.tool_name == "shell"
    assert started.tool_phase == "started"
    assert "pytest" in (started.tool_summary or "")
    assert finished.activity_id == "i_2" and finished.tool_phase == "completed"


def test_claude_tool_block_carries_activity_id_and_completes():
    parser = ClaudeStreamParser()
    parser.parse('{"type":"system","subtype":"init","session_id":"s_abc"}')
    started = parser.parse(
        '{"type":"stream_event","event":{"type":"content_block_start","index":2,'
        '"content_block":{"type":"tool_use","id":"toolu_9","name":"Read","input":{}}}}')
    finished = parser.parse(
        '{"type":"stream_event","event":{"type":"content_block_stop","index":2}}')

    assert started is not None and started.activity_id == "toolu_9"
    assert started.tool_phase == "started"
    assert finished is not None and finished.activity_id == "toolu_9"
    assert finished.tool_phase == "completed" and finished.tool_name == "Read"


def test_claude_tool_block_without_id_falls_back_to_session_and_index():
    parser = ClaudeStreamParser()
    parser.parse('{"type":"system","subtype":"init","session_id":"s_abc"}')
    ev = parser.parse(
        '{"type":"stream_event","event":{"type":"content_block_start","index":3,'
        '"content_block":{"type":"tool_use","name":"Bash","input":{}}}}')
    assert ev is not None and ev.activity_id == "s_abc:3"


def test_codex_command_completion_summarizes_exit_and_output_size():
    ev = parse_stream_line(
        "codex", '{"type":"item.completed","item":{"id":"i_7","type":"command_execution",'
                 '"command":"git status","aggregated_output":" M a.py\\n M b.py","exit_code":0}}')
    assert ev is not None
    assert ev.tool_name == "shell"
    assert ev.tool_summary == "git status → exit 0 · 15 chars"


def test_codex_web_search_summary_names_the_query_and_first_result():
    ev = parse_stream_line(
        "codex", '{"type":"item.completed","item":{"id":"i_8","type":"web_search",'
                 '"query":"https://arxiv.org/html/1","results":[{"title":"Do More Agents Help?","url":"u"}]}}')
    assert ev is not None
    assert ev.tool_name == "web_search"
    assert "Do More Agents Help?" in (ev.tool_summary or "")
    assert "1 条结果" in (ev.tool_summary or "")


def test_codex_mcp_tool_call_is_named_so_the_duplicate_can_be_filtered():
    ev = parse_stream_line(
        "codex", '{"type":"item.started","item":{"id":"i_9","type":"mcp_tool_call",'
                 '"server":"yiren_hub","tool":"log_thought","arguments":{"kind":"plan"}}}')
    assert ev is not None
    assert ev.tool_name == "mcp:yiren_hub:log_thought"
    assert "plan" in (ev.tool_summary or "")


def test_mcp_tool_names_are_filtered_from_provider_activity():
    from backend.app.adapters.mcp_subprocess import _is_mcp_tool

    assert _is_mcp_tool("mcp__yiren_hub__publish_message")
    assert _is_mcp_tool("mcp:yiren_hub:log_thought")
    assert _is_mcp_tool("mcp_tool_call")
    assert not _is_mcp_tool("shell")


def _claude_tool_call(parser, index, block_id, name, argument_json):
    import json as _json

    def line(event):
        return _json.dumps({"type": "stream_event", "event": event})

    parser.parse(line({"type": "content_block_start", "index": index,
                       "content_block": {"type": "tool_use", "id": block_id, "name": name, "input": {}}}))
    parser.parse(line({"type": "content_block_delta", "index": index,
                       "delta": {"type": "input_json_delta", "partial_json": argument_json}}))
    return parser.parse(line({"type": "content_block_stop", "index": index}))


def test_claude_read_summary_names_the_file():
    import json

    parser = ClaudeStreamParser()
    ev = _claude_tool_call(parser, 0, "toolu_1", "Read", '{"file_path": "/tmp/a.py"}')
    assert ev is not None and ev.tool_phase == "completed"
    assert ev.tool_summary == "Read · /tmp/a.py"


def test_claude_bash_summary_names_the_command():
    import json

    parser = ClaudeStreamParser()
    ev = _claude_tool_call(parser, 1, "toolu_2", "Bash", '{"command": "pytest -q", "description": "run tests"}')
    assert ev.tool_summary == "Bash · pytest -q"


def test_claude_summary_falls_back_to_raw_json_when_unparsable():
    parser = ClaudeStreamParser()
    parser.parse('{"type":"stream_event","event":{"type":"content_block_start","index":2,'
                 '"content_block":{"type":"tool_use","id":"toolu_3","name":"Grep","input":{}}}}')
    parser.parse('{"type":"stream_event","event":{"type":"content_block_delta","index":2,'
                 '"delta":{"type":"input_json_delta","partial_json":"{\\"pattern\\": \\"def "}}}')
    ev = parser.parse('{"type":"stream_event","event":{"type":"content_block_stop","index":2}}')
    assert ev is not None and ev.tool_summary is not None
    assert ev.tool_summary.startswith("Grep · ")


def test_claude_tool_result_carries_output_and_ok():
    import json

    parser = ClaudeStreamParser()
    parser.parse('{"type":"system","subtype":"init","session_id":"s_1"}')
    parser.parse(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": 2,
        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}}}}))
    parser.parse(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta", "index": 2,
        "delta": {"type": "input_json_delta", "partial_json": '{"file_path": "/tmp/a.py"}'}}}))
    completed = parser.parse(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_stop", "index": 2}}))
    assert completed.tool_args == {"file_path": "/tmp/a.py"}

    result = parser.parse(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file body", "is_error": False}]}}))
    assert result is not None
    assert result.tool_phase == "result"
    assert result.tool_name == "Read"
    assert result.tool_output == "file body"
    assert result.tool_ok is True


def test_claude_tool_result_error_is_marked_failed():
    import json

    parser = ClaudeStreamParser()
    parser.parse(json.dumps({"type": "stream_event", "event": {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {}}}}))
    result = parser.parse(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": "denied", "is_error": True}]}}))
    assert result.tool_phase == "failed"
    assert result.tool_ok is False
    assert result.tool_error == "denied"


def test_codex_command_records_args_output_and_exit_status():
    started = parse_stream_line(
        "codex", '{"type":"item.started","item":{"id":"i_1","type":"command_execution","command":"git status"}}')
    done = parse_stream_line(
        "codex", '{"type":"item.completed","item":{"id":"i_1","type":"command_execution",'
                 '"command":"git status","aggregated_output":" M a.py","exit_code":0,"status":"completed"}}')
    assert started.tool_args == {"command": "git status"}
    assert done.tool_output == " M a.py"
    assert done.tool_ok is True


def test_codex_command_failure_marks_ok_false():
    done = parse_stream_line(
        "codex", '{"type":"item.completed","item":{"id":"i_2","type":"command_execution",'
                 '"command":"pytest","aggregated_output":"1 failed","exit_code":1,"status":"completed"}}')
    assert done.tool_ok is False


def test_claude_usage_records_model_and_stop_reason():
    line = ('{"type":"result","subtype":"success","is_error":false,"usage":{"input_tokens":1,"output_tokens":2},'
            '"modelUsage":{"claude-haiku-4-5":{"inputTokens":1}},"stop_reason":"end_turn"}')
    ev = parse_stream_line("claude", line)
    assert ev.usage.model == "claude-haiku-4-5"
    assert ev.usage.stop_reason == "end_turn"
