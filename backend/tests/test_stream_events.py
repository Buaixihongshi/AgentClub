from backend.app.adapters.stream_events import StreamEvent, StreamEventType


def test_stream_event_partial_text_required_fields():
    ev = StreamEvent(type=StreamEventType.PARTIAL_TEXT, text="王也", session_id=None, tool_name=None)
    assert ev.type is StreamEventType.PARTIAL_TEXT
    assert ev.text == "王也"


def test_stream_event_session_started_carries_session_id():
    ev = StreamEvent(type=StreamEventType.SESSION_STARTED, text=None, session_id="s_x", tool_name=None)
    assert ev.session_id == "s_x"


def test_stream_event_tool_attempt_carries_tool_name():
    ev = StreamEvent(type=StreamEventType.TOOL_ATTEMPT, text=None, session_id=None, tool_name="publish_message")
    assert ev.tool_name == "publish_message"
