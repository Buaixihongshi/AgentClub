import json
from typing import Optional
from backend.app.adapters.stream_events import StreamEvent, StreamEventType, UsageRecord

PUBLISH_MESSAGE_TOOL = "mcp__yiren_hub__publish_message"

# Argument keys that answer "what was this call about" for the native tools.
_SUMMARY_KEYS = ("file_path", "path", "notebook_path", "command", "pattern", "query",
                 "url", "description", "prompt")
_SUMMARY_CHARS = 200


def _clip(value: str, limit: int = _SUMMARY_CHARS) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short_name(name: str) -> str:
    return name.split("__")[-1] if "__" in name else name


def _tool_summary(tool_name: str, raw_json: str) -> Optional[str]:
    """The one line a reader wants: `Read · /path` or `Bash · git status`."""
    text = (raw_json or "").strip()
    if not text:
        return None
    try:
        args = json.loads(text)
    except ValueError:
        return "{} · {}".format(_short_name(tool_name), _clip(text, 120))
    if not isinstance(args, dict):
        return None
    for key in _SUMMARY_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return "{} · {}".format(_short_name(tool_name), _clip(value))
    return None


class ClaudeStreamParser:
    def __init__(self):
        self._publish_block_index: Optional[int] = None
        self._publish_args = ""
        self._published_content = ""
        self._session_id: Optional[str] = None
        self._model: Optional[str] = None
        self._activity_names: dict[str, str] = {}
        self._queued: list[StreamEvent] = []
        # index -> (activity_id, tool_name) so a content_block_stop can report
        # the completion of a call it saw start.
        self._tool_blocks: dict[int, tuple[str, str]] = {}
        # index -> accumulated input JSON, for a summary at block stop
        self._tool_args: dict[int, str] = {}

    def parse(self, line: str) -> Optional[StreamEvent]:
        if self._queued:
            return self._queued.pop(0)
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
                model = event.get("model")
                if isinstance(model, str) and model:
                    self._model = model
                if isinstance(sid, str):
                    self._session_id = sid
                    return StreamEvent(type=StreamEventType.SESSION_STARTED, session_id=sid,
                                       model=self._model)
            return None
        if etype == "stream_event":
            return self._parse_stream_event(event)
        if etype == "user":
            return self._parse_tool_results(event)
        if etype == "result":
            if event.get("is_error") is True:
                err = event.get("result")
                return StreamEvent(type=StreamEventType.ERROR, error=str(err) if err else None)
            if event.get("subtype") == "success":
                usage = _parse_usage(event)
                return StreamEvent(type=StreamEventType.FINISHED, usage=usage)
            return None
        return None

    def _parse_tool_results(self, event: dict) -> Optional[StreamEvent]:
        message = event.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        if not isinstance(blocks, list):
            return None
        results = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            name = self._activity_names.pop(tool_use_id, "") or "tool"
            text = _flatten_result_content(block.get("content"))
            is_error = block.get("is_error") is True
            results.append(StreamEvent(
                type=StreamEventType.TOOL_ATTEMPT,
                tool_name=name,
                activity_id=tool_use_id,
                tool_phase="failed" if is_error else "result",
                tool_output=text,
                tool_ok=not is_error,
                tool_error=text if is_error else None,
            ))
        if not results:
            return None
        self._queued.extend(results[1:])
        return results[0]

    def _parse_stream_event(self, event: dict) -> Optional[StreamEvent]:
        inner = event.get("event")
        if not isinstance(inner, dict):
            return None
        inner_type = inner.get("type")
        if inner_type == "content_block_start":
            block = inner.get("content_block")
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                return None
            tool_name = block.get("name")
            index = inner.get("index")
            if tool_name == PUBLISH_MESSAGE_TOOL:
                self._publish_block_index = index if isinstance(index, int) else None
                self._publish_args = ""
                self._published_content = ""
            activity_id = block.get("id")
            if not isinstance(activity_id, str) or not activity_id:
                activity_id = "{session}:{index}".format(
                    session=self._session_id or "claude",
                    index=index if isinstance(index, int) else 0,
                )
            if isinstance(tool_name, str) and isinstance(index, int):
                self._tool_blocks[index] = (activity_id, tool_name)
                self._tool_args[index] = ""
            if isinstance(activity_id, str) and isinstance(tool_name, str):
                self._activity_names[activity_id] = tool_name
            inline = block.get("input")
            summary = _tool_summary(tool_name or "", json.dumps(inline)) if isinstance(inline, dict) and inline else None
            return StreamEvent(
                type=StreamEventType.TOOL_ATTEMPT,
                tool_name=tool_name if isinstance(tool_name, str) else None,
                activity_id=activity_id,
                tool_phase="started",
                tool_summary=summary,
                tool_args=inline if isinstance(inline, dict) and inline else None,
            )
        if inner_type == "content_block_stop":
            index = inner.get("index")
            if index == self._publish_block_index:
                self._publish_block_index = None
                self._publish_args = ""
                self._published_content = ""
            if isinstance(index, int) and index in self._tool_blocks:
                activity_id, tool_name = self._tool_blocks.pop(index)
                raw = self._tool_args.pop(index, "")
                parsed_args = _parse_args(raw)
                return StreamEvent(
                    type=StreamEventType.TOOL_ATTEMPT,
                    tool_name=tool_name,
                    activity_id=activity_id,
                    tool_phase="completed",
                    tool_summary=_tool_summary(tool_name, raw),
                    tool_args=parsed_args,
                )
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
        if delta.get("type") == "input_json_delta":
            index = inner.get("index")
            partial = delta.get("partial_json")
            if not isinstance(partial, str):
                return None
            if isinstance(index, int):
                self._tool_args[index] = self._tool_args.get(index, "") + partial
        if delta.get("type") == "input_json_delta" and inner.get("index") == self._publish_block_index:
            self._publish_args += partial
            content = _extract_content_prefix(self._publish_args)
            if not content or content == self._published_content:
                return None
            text = content[len(self._published_content):]
            self._published_content = content
            if text:
                return StreamEvent(type=StreamEventType.PARTIAL_TEXT, text=text)
        return None


def _flatten_result_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _parse_args(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


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
    model = ""
    if isinstance(model_usage, dict):
        provider_raw["modelUsage"] = model_usage
        model = next((str(key) for key in model_usage if isinstance(key, str)), "")
    return UsageRecord(
        input_tokens=raw.get("input_tokens", 0),
        cached_input_tokens=raw.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=raw.get("cache_creation_input_tokens", 0),
        output_tokens=raw.get("output_tokens", 0),
        total_cost_usd=float(cost) if cost is not None else None,
        provider_raw=provider_raw,
        model=model,
        stop_reason=event.get("stop_reason") if isinstance(event.get("stop_reason"), str) else None,
    )
