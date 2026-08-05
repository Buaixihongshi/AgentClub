import json
from typing import Optional
from backend.app.adapters.stream_events import StreamEvent, StreamEventType, UsageRecord


def parse(line: str) -> Optional[StreamEvent]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    etype = event.get("type")
    if etype == "thread.started":
        tid = event.get("thread_id")
        if isinstance(tid, str):
            return StreamEvent(type=StreamEventType.SESSION_STARTED, session_id=tid)
    elif etype == "item.completed":
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text:
                return StreamEvent(type=StreamEventType.PARTIAL_TEXT, text=text)
    elif etype == "turn.completed":
        usage = _parse_usage(event)
        return StreamEvent(type=StreamEventType.FINISHED, usage=usage)
    return None


def _parse_usage(event: dict) -> Optional[UsageRecord]:
    raw = event.get("usage")
    if not isinstance(raw, dict):
        return None
    return UsageRecord(
        input_tokens=raw.get("input_tokens", 0),
        cached_input_tokens=raw.get("cached_input_tokens", 0),
        output_tokens=raw.get("output_tokens", 0),
        reasoning_output_tokens=raw.get("reasoning_output_tokens", 0),
        provider_raw=dict(raw),
    )
