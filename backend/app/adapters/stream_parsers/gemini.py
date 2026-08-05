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
    if etype == "init":
        sid = event.get("session_id")
        if isinstance(sid, str):
            return StreamEvent(type=StreamEventType.SESSION_STARTED, session_id=sid)
    elif etype == "message" and event.get("role") == "assistant":
        content = event.get("content")
        if isinstance(content, str) and content:
            return StreamEvent(type=StreamEventType.PARTIAL_TEXT, text=content)
    elif etype == "tool_use":
        name = event.get("tool_name")
        if isinstance(name, str):
            return StreamEvent(type=StreamEventType.TOOL_ATTEMPT, tool_name=name)
    elif etype == "result":
        usage = _parse_usage(event)
        status = event.get("status")
        if status == "success":
            return StreamEvent(type=StreamEventType.FINISHED, usage=usage)
        err = event.get("error")
        return StreamEvent(type=StreamEventType.ERROR, error=str(err) if err else None, usage=usage)
    return None


def _parse_usage(event: dict) -> Optional[UsageRecord]:
    stats = event.get("stats")
    if not isinstance(stats, dict):
        return None
    return UsageRecord(
        input_tokens=stats.get("input_tokens", 0),
        cached_input_tokens=stats.get("cached", 0),
        output_tokens=stats.get("output_tokens", 0),
        duration_ms=stats.get("duration_ms", 0),
        provider_raw=dict(stats),
    )
