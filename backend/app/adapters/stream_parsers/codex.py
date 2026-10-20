import json
from typing import Optional
from backend.app.adapters.stream_events import StreamEvent, StreamEventType, UsageRecord

_SUMMARY_CHARS = 200


def _clip(value: object, limit: int = _SUMMARY_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_detail(item: dict, item_type: str, phase: str) -> dict:
    """Name, summary and structured detail for one native tool item.

    Codex reports the interesting part inside `arguments` / `command` /
    `results` / `aggregated_output`, not in `name` — without lifting it out,
    the observability panel can only say "a call happened".
    """
    result: dict = {"tool_name": item_type, "summary": "", "args": None,
                    "output": None, "ok": None, "error": None}
    error = item.get("error")
    if isinstance(error, str) and error:
        result["error"] = error
    status = item.get("status")
    if phase != "started":
        result["ok"] = status != "failed" and not result["error"]

    if item_type == "command_execution":
        command = _clip(item.get("command"), 120)
        result["tool_name"] = "shell"
        result["args"] = {"command": item.get("command")} if item.get("command") else None
        if phase == "started":
            result["summary"] = command
            return result
        output = item.get("aggregated_output")
        size = len(output) if isinstance(output, str) else 0
        result["summary"] = "{cmd} → exit {code} · {size} chars".format(
            cmd=command, code=item.get("exit_code"), size=size)
        result["output"] = output if isinstance(output, str) else None
        exit_code = item.get("exit_code")
        if isinstance(exit_code, int):
            result["ok"] = exit_code == 0 and not result["error"]
        return result

    if item_type == "web_search":
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        query = item.get("query") or action.get("url") or ""
        results = item.get("results") if isinstance(item.get("results"), list) else []
        result["tool_name"] = "web_search"
        result["args"] = {"query": query} if query else None
        if phase == "completed" and results:
            first = results[0] if isinstance(results[0], dict) else {}
            title = first.get("title") or first.get("url") or ""
            result["summary"] = "{q} · {n} 条结果：{t}".format(
                q=_clip(query, 70), n=len(results), t=_clip(title, 70))
            result["output"] = "\n".join(
                "{t} — {u}".format(t=(r.get("title") or ""), u=(r.get("url") or ""))
                for r in results if isinstance(r, dict))
        else:
            result["summary"] = _clip(query)
        return result

    if item_type == "mcp_tool_call":
        server = item.get("server")
        tool = item.get("tool") or item.get("name") or "mcp_tool_call"
        result["tool_name"] = "mcp:{server}:{tool}".format(server=server, tool=tool) if server else str(tool)
        arguments = item.get("arguments")
        result["args"] = arguments if isinstance(arguments, dict) else None
        payload = item.get("result")
        if payload is not None:
            result["output"] = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        result["summary"] = _clip(json.dumps(arguments, ensure_ascii=False)) if arguments else ""
        return result

    name = item.get("name") or item.get("tool") or item_type
    result["tool_name"] = str(name)
    arguments = item.get("arguments")
    result["args"] = arguments if isinstance(arguments, dict) else None
    result["summary"] = _clip(json.dumps(arguments, ensure_ascii=False)) if arguments else ""
    payload = item.get("result")
    if payload is not None:
        result["output"] = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    return result


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
    elif etype in {"item.started", "item.completed"}:
        item = event.get("item")
        if not isinstance(item, dict):
            return None
        item_type = item.get("type")
        if item_type == "agent_message" and etype == "item.completed":
            text = item.get("text")
            if isinstance(text, str) and text:
                return StreamEvent(type=StreamEventType.PARTIAL_TEXT, text=text)
        if item_type in {
            "command_execution",
            "dynamic_tool_call",
            "mcp_tool_call",
            "web_search",
        }:
            phase = "started" if etype == "item.started" else "completed"
            detail = _tool_detail(item, item_type, phase)
            # An attempt without an activity id never reaches
            # `provider.activity`, so codex turns looked like they only ever
            # spoke. The item id is the call's stable identity.
            activity_id = item.get("id") or item.get("call_id")
            return StreamEvent(
                type=StreamEventType.TOOL_ATTEMPT,
                tool_name=detail["tool_name"],
                activity_id=str(activity_id) if isinstance(activity_id, str) and activity_id else None,
                tool_phase=phase,
                tool_summary=detail["summary"] or None,
                tool_args=detail["args"],
                tool_output=detail["output"],
                tool_ok=detail["ok"],
                tool_error=detail["error"],
            )
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
