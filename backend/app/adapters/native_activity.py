"""Shared projection of provider stream events into trace events.

``provider.session`` names the native session once per turn; ``provider.activity``
records native tool use (excluding Unity's own MCP tools, which already have
``tool.call`` events) with sanitized args/output and the call duration.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from backend.app.adapters.stream_events import StreamEvent, StreamEventType
from backend.app.orchestration.events import sanitize_tool_args, sanitize_tool_output


def is_mcp_tool(name: str) -> bool:
    """True for the agent's own MCP tools: they already have a `tool.call` event."""
    return name in {"call_mcp_tool", "mcp_tool_call"} or name.startswith(("mcp__", "mcp:"))


class NativeActivityRecorder:
    def __init__(self, add_event: Callable[[str, dict], None], *, turn_id: str, agent_id: str, provider_id: str,
                 on_session: Optional[Callable[[str, Optional[str]], None]] = None):
        self.add_event = add_event
        self.turn_id = turn_id
        self.agent_id = agent_id
        self.provider_id = provider_id
        self.on_session = on_session
        self.session_id: Optional[str] = None
        self._started: dict[str, float] = {}

    def handle(self, ev: StreamEvent) -> None:
        if ev.type is StreamEventType.SESSION_STARTED and ev.session_id:
            first = self.session_id is None
            self.session_id = ev.session_id
            if first:
                self.add_event("provider.session", {
                    "turn_id": self.turn_id, "agent_id": self.agent_id,
                    "provider_id": self.provider_id, "session_id": ev.session_id,
                    "model": ev.model,
                })
                if self.on_session is not None:
                    self.on_session(ev.session_id, ev.model)
        if ev.activity_id and ev.tool_name and not is_mcp_tool(ev.tool_name):
            payload = {
                "turn_id": self.turn_id, "agent_id": self.agent_id,
                "provider_id": self.provider_id, "activity_id": ev.activity_id,
                "tool_name": ev.tool_name, "phase": ev.tool_phase,
                "summary": ev.tool_summary,
            }
            args = sanitize_tool_args(ev.tool_args)
            if args:
                payload["args"] = args
            if ev.tool_output:
                payload["output"] = sanitize_tool_output(ev.tool_output)
            if ev.tool_error:
                payload["error"] = sanitize_tool_output(ev.tool_error)[:500]
            if ev.tool_ok is not None:
                payload["ok"] = ev.tool_ok
            started_at = self._started.get(ev.activity_id)
            if ev.tool_phase == "started":
                self._started[ev.activity_id] = time.monotonic()
            elif started_at is not None:
                payload["duration_ms"] = int((time.monotonic() - started_at) * 1000)
                self._started.pop(ev.activity_id, None)
            self.add_event("provider.activity", payload)
