from datetime import datetime, timezone
from typing import Optional

from backend.app.storage.repository import Repository


def record_turn_started(
    repo: Repository,
    mission_id: str,
    turn_id: str,
    agent_id: str,
    provider_id: str,
    attempt: int,
    purpose: str,
    started_at: Optional[datetime] = None,
    a2a_sender_turn_id: Optional[str] = None,
) -> None:
    payload: dict = {
        "mission_id": mission_id,
        "turn_id": turn_id,
        "agent_id": agent_id,
        "provider_id": provider_id,
        "attempt": attempt,
        "purpose": purpose,
        "started_at": (started_at or datetime.now(timezone.utc)).isoformat(),
    }
    if a2a_sender_turn_id is not None:
        payload["a2a_sender_turn_id"] = a2a_sender_turn_id
    repo.add_event(mission_id, "turn.started", payload)


def record_turn_completed(
    repo: Repository,
    mission_id: str,
    turn_id: str,
    agent_id: str,
    provider_id: str,
    attempt: int,
    duration_ms: int,
    summary: str = "",
) -> None:
    repo.add_event(
        mission_id,
        "turn.completed",
        {
            "mission_id": mission_id,
            "turn_id": turn_id,
            "agent_id": agent_id,
            "provider_id": provider_id,
            "attempt": attempt,
            "duration_ms": duration_ms,
            "summary": summary,
        },
    )


def record_turn_blocked(
    repo: Repository,
    mission_id: str,
    turn_id: str,
    agent_id: str,
    provider_id: str,
    attempt: int,
    duration_ms: int,
    error: str,
) -> None:
    repo.add_event(
        mission_id,
        "turn.blocked",
        {
            "mission_id": mission_id,
            "turn_id": turn_id,
            "agent_id": agent_id,
            "provider_id": provider_id,
            "attempt": attempt,
            "duration_ms": duration_ms,
            "error": error,
        },
    )


def record_turn_failed(
    repo: Repository,
    mission_id: str,
    turn_id: str,
    agent_id: str,
    provider_id: str,
    attempt: int,
    duration_ms: int,
    error: str,
) -> None:
    repo.add_event(
        mission_id,
        "turn.failed",
        {
            "mission_id": mission_id,
            "turn_id": turn_id,
            "agent_id": agent_id,
            "provider_id": provider_id,
            "attempt": attempt,
            "duration_ms": duration_ms,
            "error": error,
        },
    )


def record_tool_call(
    repo: Repository,
    mission_id: str,
    turn_id: str,
    agent_id: str,
    tool_name: str,
    tool_input: Optional[dict] = None,
    provider_id: Optional[str] = None,
) -> None:
    sanitized_input = _sanitize_tool_input(tool_name, tool_input)
    repo.add_event(
        mission_id,
        "tool.call",
        {
            "mission_id": mission_id,
            "turn_id": turn_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "tool_input": sanitized_input,
            "provider_id": provider_id,
        },
    )


def record_tool_result(
    repo: Repository,
    mission_id: str,
    turn_id: str,
    agent_id: str,
    tool_name: str,
    summary: str = "",
    artifact_id: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> None:
    repo.add_event(
        mission_id,
        "tool.result",
        {
            "mission_id": mission_id,
            "turn_id": turn_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "summary": summary,
            "artifact_id": artifact_id,
            "provider_id": provider_id,
        },
    )


def record_tool_error(
    repo: Repository,
    mission_id: str,
    turn_id: str,
    agent_id: str,
    tool_name: str,
    error: str,
    provider_id: Optional[str] = None,
) -> None:
    repo.add_event(
        mission_id,
        "tool.error",
        {
            "mission_id": mission_id,
            "turn_id": turn_id,
            "agent_id": agent_id,
            "tool_name": tool_name,
            "error": error,
            "provider_id": provider_id,
        },
    )


def _sanitize_tool_input(tool_name: str, tool_input: Optional[dict]) -> Optional[dict]:
    if tool_input is None:
        return None
    sanitized = dict(tool_input)
    if tool_name == "write_artifact":
        sanitized.pop("content", None)
    for key in list(sanitized.keys()):
        value = sanitized[key]
        if isinstance(value, str) and len(value) > 200:
            sanitized[key] = value[:200] + "...[truncated]"
    return sanitized


def record_interrupt_handled(
    repo: Repository,
    mission_id: str,
    interrupt_event_id: str,
    target_agent_id: Optional[str],
    handled_by_agent_id: str,
    turn_id: str,
) -> None:
    repo.add_event(
        mission_id,
        "interrupt.handled",
        {
            "mission_id": mission_id,
            "interrupt_event_id": interrupt_event_id,
            "target_agent_id": target_agent_id,
            "handled_by_agent_id": handled_by_agent_id,
            "turn_id": turn_id,
            "handled_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def record_a2a_invoke(
    repo: Repository,
    mission_id: str,
    sender_agent_id: str,
    sender_turn_id: str,
    target_agent_id: str,
    purpose: str,
    mode: str,
    queued_position: int,
) -> None:
    repo.add_event(
        mission_id,
        "a2a.invoke",
        {
            "mission_id": mission_id,
            "sender_agent_id": sender_agent_id,
            "sender_turn_id": sender_turn_id,
            "target_agent_id": target_agent_id,
            "purpose": purpose,
            "mode": mode,
            "queued_position": queued_position,
        },
    )


def record_a2a_skipped(
    repo: Repository,
    mission_id: str,
    sender_agent_id: str,
    sender_turn_id: str,
    target_agent_id: str,
    purpose: str,
    reason: str,
) -> None:
    """A fire-and-forget invoke that was absorbed instead of queued, because the
    target is already scheduled to speak (avoids a redundant duplicate turn)."""
    repo.add_event(
        mission_id,
        "a2a.skipped",
        {
            "mission_id": mission_id,
            "sender_agent_id": sender_agent_id,
            "sender_turn_id": sender_turn_id,
            "target_agent_id": target_agent_id,
            "purpose": purpose,
            "reason": reason,
        },
    )


def record_a2a_cancelled(
    repo: Repository,
    mission_id: str,
    pending_count: int,
    reason: str,
    cancelled_steps: Optional[list] = None,
) -> None:
    payload: dict = {
        "mission_id": mission_id,
        "pending_count": pending_count,
        "reason": reason,
    }
    if cancelled_steps is not None:
        payload["cancelled_steps"] = cancelled_steps
    repo.add_event(mission_id, "a2a.cancelled", payload)


def record_a2a_reply(
    repo: Repository,
    mission_id: str,
    *,
    callee_turn_id: str,
    callee_agent_id: str,
    invoke_event_id: str,
    sender_agent_id: str,
    sender_resume_step_id: str,
    reply_text: str,
    reply_message_id: Optional[str],
    truncated: bool,
) -> None:
    repo.add_event(
        mission_id,
        "a2a.reply",
        {
            "mission_id": mission_id,
            "turn_id": callee_turn_id,
            "agent_id": callee_agent_id,
            "invoke_event_id": invoke_event_id,
            "sender_agent_id": sender_agent_id,
            "sender_resume_step_id": sender_resume_step_id,
            "reply_text": reply_text,
            "reply_message_id": reply_message_id,
            "truncated": truncated,
        },
    )


def record_a2a_reply_failed(
    repo: Repository,
    mission_id: str,
    *,
    callee_turn_id: str,
    callee_agent_id: str,
    invoke_event_id: str,
    reason: str,
    fallback_reply_text: str,
) -> None:
    repo.add_event(
        mission_id,
        "a2a.reply.failed",
        {
            "mission_id": mission_id,
            "turn_id": callee_turn_id,
            "agent_id": callee_agent_id,
            "invoke_event_id": invoke_event_id,
            "reason": reason,
            "fallback_reply_text": fallback_reply_text,
        },
    )


def record_a2a_request(
    repo: Repository,
    mission_id: str,
    sender_agent_id: str,
    sender_turn_id: str,
    target_agent_id: str,
    purpose: str,
    mode: str,
) -> None:
    """Persists an A2A request from a subprocess MCP call.

    Written when MCPTools.invoke_agent is called without an in-memory
    a2a_enqueue callback (i.e. from a real CLI subprocess).  The runner
    polls for these events after each turn and promotes them into the
    worklist.
    """
    repo.add_event(
        mission_id,
        "a2a.request",
        {
            "mission_id": mission_id,
            "sender_agent_id": sender_agent_id,
            "sender_turn_id": sender_turn_id,
            "target_agent_id": target_agent_id,
            "purpose": purpose,
            "mode": mode,
        },
    )


def fetch_a2a_requests_for_turn(repo: Repository, mission_id: str, sender_turn_id: str) -> list:
    """Return all a2a.request events whose sender_turn_id matches the given turn."""
    return [
        event
        for event in repo.list_events_after(mission_id, 0)
        if event.event_type == "a2a.request"
        and event.payload.get("sender_turn_id") == sender_turn_id
    ]


def find_pending_interrupts(repo: Repository, mission_id: str) -> list:
    """Return interrupt events that have no matching interrupt.handled event."""
    events = repo.list_events_after(mission_id, 0)
    interrupt_ids = set()
    handled_ids = set()
    interrupt_events = []
    for event in events:
        if event.event_type == "interrupt":
            interrupt_ids.add(str(event.id))
            interrupt_events.append(event)
        elif event.event_type == "interrupt.handled":
            handled_ids.add(str(event.payload.get("interrupt_event_id", "")))
    return [e for e in interrupt_events if str(e.id) not in handled_ids]
