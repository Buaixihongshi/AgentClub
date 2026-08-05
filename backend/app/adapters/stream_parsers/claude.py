import json
from typing import Optional
from backend.app.adapters.stream_events import StreamEvent, StreamEventType, UsageRecord

PUBLISH_MESSAGE_TOOL = "mcp__yiren_hub__publish_message"


class ClaudeStreamParser:
    def __init__(self):
        self._publish_block_index: Optional[int] = None
        self._publish_args = ""
        self._published_content = ""

    def parse(self, line: str) -> Optional[StreamEvent]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        etype = event.get("type")
        if etype == "system":
            if event.get("subtype") == "init":
                sid = event.get("session_id")
                if isinstance(sid, str):
                    return StreamEvent(type=StreamEventType.SESSION_STARTED, session_id=sid)
            return None
        if etype == "stream_event":
            return self._parse_stream_event(event)
        if etype == "result":
            if event.get("is_error") is True:
                err = event.get("result")
                return StreamEvent(type=StreamEventType.ERROR, error=str(err) if err else None)
            if event.get("subtype") == "success":
                usage = _parse_usage(event)
                return StreamEvent(type=StreamEventType.FINISHED, usage=usage)
            return None
        return None

    def _parse_stream_event(self, event: dict) -> Optional[StreamEvent]:
        inner = event.get("event")
        if not isinstance(inner, dict):
            return None
        inner_type = inner.get("type")
        if inner_type == "content_block_start":
            block = inner.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == PUBLISH_MESSAGE_TOOL:
                index = inner.get("index")
                self._publish_block_index = index if isinstance(index, int) else None
                self._publish_args = ""
                self._published_content = ""
            return None
        if inner_type == "content_block_stop":
            if inner.get("index") == self._publish_block_index:
                self._publish_block_index = None
                self._publish_args = ""
                self._published_content = ""
            return None
        if inner_type != "content_block_delta":
            return None
        delta = inner.get("delta")
        if not isinstance(delta, dict):
            return None
        if delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                return StreamEvent(type=StreamEventType.PARTIAL_TEXT, text=text)
            return None
        if delta.get("type") == "input_json_delta" and inner.get("index") == self._publish_block_index:
            partial = delta.get("partial_json")
            if not isinstance(partial, str):
                return None
            self._publish_args += partial
            content = _extract_content_prefix(self._publish_args)
            if not content or content == self._published_content:
                return None
            text = content[len(self._published_content):]
            self._published_content = content
            if text:
                return StreamEvent(type=StreamEventType.PARTIAL_TEXT, text=text)
        return None


def parse(line: str) -> Optional[StreamEvent]:
    return ClaudeStreamParser().parse(line)


def _extract_content_prefix(buffer: str) -> str:
    marker = '"content"'
    marker_index = buffer.find(marker)
    if marker_index < 0:
        return ""
    colon_index = buffer.find(":", marker_index + len(marker))
    if colon_index < 0:
        return ""
    quote_index = colon_index + 1
    while quote_index < len(buffer) and buffer[quote_index].isspace():
        quote_index += 1
    if quote_index >= len(buffer) or buffer[quote_index] != '"':
        return ""
    return _decode_json_string_prefix(buffer[quote_index + 1:])


def _decode_json_string_prefix(value: str) -> str:
    chars: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == '"':
            break
        if ch != "\\":
            chars.append(ch)
            i += 1
            continue
        if i + 1 >= len(value):
            break
        esc = value[i + 1]
        if esc == "u":
            hex_value = value[i + 2 : i + 6]
            if len(hex_value) < 4:
                break
            try:
                chars.append(chr(int(hex_value, 16)))
            except ValueError:
                break
            i += 6
            continue
        chars.append({
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }.get(esc, esc))
        i += 2
    return "".join(chars)


def _parse_usage(event: dict) -> Optional[UsageRecord]:
    raw = event.get("usage")
    if not isinstance(raw, dict):
        return None
    cost = event.get("total_cost_usd")
    provider_raw = {"usage": dict(raw)}
    model_usage = event.get("modelUsage")
    if isinstance(model_usage, dict):
        provider_raw["modelUsage"] = model_usage
    return UsageRecord(
        input_tokens=raw.get("input_tokens", 0),
        cached_input_tokens=raw.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=raw.get("cache_creation_input_tokens", 0),
        output_tokens=raw.get("output_tokens", 0),
        total_cost_usd=float(cost) if cost is not None else None,
        provider_raw=provider_raw,
    )
