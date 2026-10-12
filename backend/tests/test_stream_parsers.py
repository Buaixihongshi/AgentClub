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
