"""Wire shape verified against local agy --output-format stream-json."""
import json
from backend.app.adapters.stream_events import StreamEventType
from backend.app.adapters.stream_parsers import StreamLineParser


def tool_event(state="ACTIVE", **overrides):
    return json.dumps({"event": "step_update", "step_update": {
        "conversation_id": "s_native", "step_index": 2, "state": state,
        "step_type": "tool", "tool_name": "view_file",
        "tool_info": {"parameters": {"AbsolutePath": "/private/project/DiscussionSetup.tsx"}, "output": "private output"},
        **overrides,
    }})


def test_native_activity_deduplicates_and_preserves_completion():
    parser = StreamLineParser("antigravity")
    assert parser.parse('{"event":"init","conversation_id":"s_native"}').session_id == "s_native"
    started = parser.parse(tool_event())
    assert started.type == StreamEventType.TOOL_ATTEMPT
    assert started.activity_id == "s_native:2"
    assert started.tool_summary == "读取文件 · DiscussionSetup.tsx"
    assert started.tool_phase == "started"
    assert parser.parse(tool_event()) is None
    finished = parser.parse(tool_event("DONE"))
    assert finished.activity_id == started.activity_id
    assert finished.tool_phase == "completed"
    assert parser.parse(tool_event("DONE")) is None


def test_native_ignores_reasoning_but_keeps_masked_summary_args_and_output():
    parser = StreamLineParser("antigravity")
    assert parser.parse(json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "thinking": "private reasoning", "text_delta": "ordinary stdout"}})) is None
    event = parser.parse(tool_event(tool_name="run_command", tool_info={"parameters": {"CommandLine": "npm test --token=secret"}, "output": "secret output"}))
    assert event.tool_summary == "运行测试"
    assert event.tool_args == {"CommandLine": "npm test --token=secret"}
    assert event.tool_output == "secret output"
    assert event.text is None


def test_sanitizers_mask_secret_shaped_values():
    from backend.app.orchestration.events import sanitize_tool_args, sanitize_tool_output

    assert sanitize_tool_args({"api_key": "abc"}) == {"api_key": "***"}
    assert sanitize_tool_output("npm test --token=secret") == "npm test --token=***"


def test_native_malformed_records_and_error_result():
    parser = StreamLineParser("antigravity")
    for value in ['garbage', '[]', '{}', '{"event":"step_update","step_update":null}', tool_event(state=[]), tool_event(step_index="bad"), tool_event(tool_name={})]:
        assert parser.parse(value) is None
    assert parser.parse('{"event":"result","result":{"status":"ERROR","response":"private"}}').type == StreamEventType.ERROR
    assert parser.parse('{"event":"result","result":{"status":"SUCCESS"}}').type == StreamEventType.FINISHED
