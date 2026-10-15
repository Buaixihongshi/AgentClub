from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from backend.app.domain.agent_registry import get_agent_registry
from backend.app.domain.models import Message, Mission
from backend.app.orchestration.live_progress import get_live_progress_registry
from backend.app.settings import Settings
from backend.app.storage.repository import Repository

_CAPABILITY_FALLBACK = {
    "planning": "planning",
    "coordination": "planning",
    "summary": "planning",
    "code": "implementation",
    "artifact": "implementation",
    "implementation": "implementation",
    "analysis": "review",
    "risk": "review",
    "comparison": "review",
}


def _agent_metadata(agent_id: str) -> dict[str, str]:
    registry = get_agent_registry()
    agent = registry.get(agent_id)
    if agent is None:
        return {}
    capability = next(
        (_CAPABILITY_FALLBACK[h] for h in agent.capability_hints if h in _CAPABILITY_FALLBACK),
        "unknown",
    )
    return {"persona_label": agent.display_name, "capability": capability}

PROVIDER_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex CLI",
    "gemini": "Gemini CLI",
    "antigravity": "Antigravity CLI",
    "fake": "演示模式",
}

STATUS_MAP = {
    "pending": "queued",
    "completed_with_warnings": "completed",
}

TERMINAL_STATUSES = {"completed", "failed", "blocked"}


def _mission_payload(mission: Mission) -> dict[str, object]:
    payload = mission.model_dump(mode="json")
    payload["status"] = STATUS_MAP.get(str(payload["status"]), payload["status"])
    return payload


def _event_metadata(provider_id: Optional[str], agent_id: Optional[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if provider_id is not None:
        metadata["provider_label"] = PROVIDER_LABELS.get(provider_id, provider_id)
    if agent_id is not None:
        metadata.update(_agent_metadata(agent_id))
    return metadata


def _message_event(message: Message, sort_key: Optional[tuple[int, int]] = None) -> dict[str, object]:
    event = {
        "id": message.id,
        "mission_id": message.mission_id,
        "event_type": "message",
        "role": message.role.value,
        "content": message.content,
        "provider_id": message.provider_id,
        "agent_id": message.agent_id,
        "turn_id": message.turn_id,
        "metadata": _event_metadata(message.provider_id, message.agent_id),
        "created_at": message.created_at.isoformat(),
    }
    if sort_key is not None:
        event["_sort_key"] = sort_key
    return event


def _raw_event(event, sort_key: Optional[tuple[int, int]] = None) -> dict[str, object]:
    payload = event.payload
    provider_id = payload.get("provider_id")
    agent_id = payload.get("agent_id") or payload.get("target_agent_id")
    content = (
        payload.get("content")
        or payload.get("summary")
        or payload.get("status")
        or event.event_type
    )
    thread_event = {
        "id": event.id,
        "mission_id": event.mission_id,
        "event_type": event.event_type,
        "role": "user" if event.event_type == "interrupt" else "system",
        "content": content,
        "provider_id": provider_id,
        "agent_id": agent_id,
        "turn_id": payload.get("turn_id"),
        "metadata": _event_metadata(provider_id, agent_id),
        "created_at": (
            event.created_at.isoformat()
            if isinstance(event.created_at, datetime)
            else event.created_at
        ),
    }
    if sort_key is not None:
        thread_event["_sort_key"] = sort_key
    return thread_event


def _strip_sort_key(event: dict[str, object]) -> dict[str, object]:
    event.pop("_sort_key", None)
    return event


def _artifact_records(settings: Settings, mission_id: str, db_events=None) -> list[dict[str, object]]:
    event_artifacts: dict[str, dict[str, object]] = {}
    if db_events:
        for event in db_events:
            if event.event_type != "artifact":
                continue
            payload = event.payload
            rel_path = payload.get("path", "")
            if not rel_path:
                continue
            artifact_id = "{mid}:{path}".format(mid=mission_id, path=rel_path)
            created_at = (
                event.created_at.isoformat()
                if isinstance(event.created_at, datetime)
                else event.created_at
            )
            from pathlib import PurePosixPath
            name = PurePosixPath(rel_path).name
            suffix = PurePosixPath(rel_path).suffix
            event_artifacts[rel_path] = {
                "id": artifact_id,
                "mission_id": mission_id,
                "title": payload.get("title", name),
                "kind": suffix.lstrip(".") or "file",
                "path": rel_path,
                "created_at": created_at,
                "agent_id": payload.get("agent_id"),
                "turn_id": payload.get("turn_id"),
                "source_event_id": event.id,
            }

    mission_dir = settings.artifact_root / mission_id
    if mission_dir.exists():
        for path in sorted(mission_dir.glob("**/*")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(mission_dir).as_posix()
            if relative_path in event_artifacts:
                continue
            created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            event_artifacts[relative_path] = {
                "id": "{mid}:{path}".format(mid=mission_id, path=relative_path),
                "mission_id": mission_id,
                "title": path.name,
                "kind": path.suffix.lstrip(".") or "file",
                "path": relative_path,
                "created_at": created_at,
                "agent_id": None,
                "turn_id": None,
                "source_event_id": None,
            }

    return list(event_artifacts.values())


ARTIFACT_CONTENT_MAX_CHARS = 200_000


def read_artifact_content(settings: Settings, mission_id: str, relative_path: str) -> dict[str, object]:
    """Read a single artifact's raw text for UI display.

    Unlike the agent-facing ``MCPTools.read_artifact`` tool, this writes no
    tool.* events (it is a user viewing a file, not an agent step) and returns
    the content verbatim without line numbers. Confined to
    ``artifact_root/<mission_id>/`` via resolve()+relative_to; a path that
    escapes raises 400, a missing file raises 404.
    """
    from pathlib import PurePosixPath

    mission_dir = (settings.artifact_root / mission_id).resolve()
    try:
        target = (mission_dir / relative_path).resolve()
        normalized = target.relative_to(mission_dir).as_posix()
    except ValueError:
        raise HTTPException(status_code=400, detail="Artifact path is outside artifact directory")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found: {p}".format(p=relative_path))
    raw = target.read_text(encoding="utf-8", errors="replace")
    total_chars = len(raw)
    truncated = total_chars > ARTIFACT_CONTENT_MAX_CHARS
    content = raw[:ARTIFACT_CONTENT_MAX_CHARS] if truncated else raw
    return {
        "path": normalized,
        "content": content,
        "kind": PurePosixPath(normalized).suffix.lstrip(".") or "file",
        "total_chars": total_chars,
        "truncated": truncated,
    }


def _derive_turns(db_events) -> list[dict[str, object]]:
    """Derive turn records from turn.* events, grouped by turn_id.

    Attempts are aggregated by (attempt, provider_id) so that a
    turn.started + turn.completed pair for the same provider attempt
    produces one attempt record, not two.
    """
    turns_by_id: dict[str, dict[str, object]] = {}
    for event in db_events:
        if not event.event_type.startswith("turn."):
            continue
        payload = event.payload
        turn_id = payload.get("turn_id", "")
        if not turn_id:
            continue

        if turn_id not in turns_by_id:
            turns_by_id[turn_id] = {
                "turn_id": turn_id,
                "agent_id": payload.get("agent_id"),
                "provider_id": payload.get("provider_id"),
                "status": "started",
                "attempts": {},
                "started_at": payload.get("started_at"),
                "completed_at": None,
                "duration_ms": None,
                "message_ids": [],
            }

        turn = turns_by_id[turn_id]
        attempt_num = payload.get("attempt", 1)
        provider_id = payload.get("provider_id", "")
        attempt_key = (attempt_num, provider_id)

        if attempt_key not in turn["attempts"]:
            turn["attempts"][attempt_key] = {
                "attempt": attempt_num,
                "provider_id": provider_id,
                "status": "started",
            }

        attempt_rec = turn["attempts"][attempt_key]

        if event.event_type == "turn.completed":
            attempt_rec["status"] = "completed"
        elif event.event_type == "turn.blocked":
            attempt_rec["status"] = "blocked"
        elif event.event_type == "turn.failed":
            attempt_rec["status"] = "failed"

        if payload.get("duration_ms") is not None:
            attempt_rec["duration_ms"] = payload["duration_ms"]
        if payload.get("error"):
            attempt_rec["error"] = payload["error"]
        if payload.get("summary"):
            attempt_rec["summary"] = payload["summary"]

        if event.event_type == "turn.completed":
            turn["status"] = "completed"
            turn["provider_id"] = payload.get("provider_id")
            turn["duration_ms"] = payload.get("duration_ms")
            turn["completed_at"] = (
                event.created_at.isoformat()
                if isinstance(event.created_at, datetime)
                else event.created_at
            )
        elif event.event_type == "turn.blocked":
            if turn["status"] != "completed":
                turn["status"] = "blocked"
        elif event.event_type == "turn.failed":
            turn["status"] = "failed"

    for turn in turns_by_id.values():
        turn["attempts"] = list(turn["attempts"].values())

    for event in db_events:
        if event.event_type != "message":
            continue
        msg_turn_id = event.payload.get("turn_id")
        msg_id = event.payload.get("id")
        if msg_turn_id and msg_turn_id in turns_by_id and msg_id:
            turns_by_id[msg_turn_id]["message_ids"].append(msg_id)

    return list(turns_by_id.values())


def _derive_tool_events(db_events) -> list[dict[str, object]]:
    """Derive tool event records from tool.call / tool.result / tool.error events."""
    tool_events = []
    for event in db_events:
        if not event.event_type.startswith("tool."):
            continue
        payload = event.payload
        created_at = (
            event.created_at.isoformat()
            if isinstance(event.created_at, datetime)
            else event.created_at
        )
        record = {
            "id": event.id,
            "mission_id": event.mission_id,
            "event_type": event.event_type,
            "tool_name": payload.get("tool_name"),
            "agent_id": payload.get("agent_id"),
            "turn_id": payload.get("turn_id"),
            "provider_id": payload.get("provider_id"),
            "created_at": created_at,
        }
        if payload.get("tool_input") is not None:
            record["tool_input"] = payload["tool_input"]
        if payload.get("artifact_id") is not None:
            record["artifact_id"] = payload["artifact_id"]
        if payload.get("summary"):
            record["summary"] = payload["summary"]
        if payload.get("error"):
            record["error"] = payload["error"]
        tool_events.append(record)
    return tool_events


def _derive_interrupts(db_events) -> list[dict[str, object]]:
    """Derive interrupt records from interrupt and interrupt.handled events."""
    interrupts: dict[str, dict[str, object]] = {}
    for event in db_events:
        if event.event_type == "interrupt":
            payload = event.payload
            created_at = (
                event.created_at.isoformat()
                if isinstance(event.created_at, datetime)
                else event.created_at
            )
            interrupts[str(event.id)] = {
                "event_id": event.id,
                "content": payload.get("content", ""),
                "target_agent_id": payload.get("target_agent_id"),
                "status": "pending",
                "created_at": created_at,
                "handled_at": None,
                "handled_by_agent_id": None,
                "turn_id": None,
            }
        elif event.event_type == "interrupt.handled":
            payload = event.payload
            interrupt_event_id = str(payload.get("interrupt_event_id", ""))
            if interrupt_event_id in interrupts:
                created_at = (
                    event.created_at.isoformat()
                    if isinstance(event.created_at, datetime)
                    else event.created_at
                )
                interrupts[interrupt_event_id]["status"] = "handled"
                interrupts[interrupt_event_id]["handled_at"] = created_at
                interrupts[interrupt_event_id]["handled_by_agent_id"] = payload.get("handled_by_agent_id")
                interrupts[interrupt_event_id]["turn_id"] = payload.get("turn_id")
    return list(interrupts.values())


def _derive_a2a_invocations(db_events) -> list[dict[str, object]]:
    """Derive A2A invocation records from a2a.invoke events.

    Also attaches target_turn_id by correlating with turn.started events
    that carry an a2a_sender_turn_id field (set by the runner when it starts
    an A2A-originated turn).
    """
    # Build lookup: sender_turn_id → list of target turn_ids (in order)
    a2a_turns: dict[str, list[str]] = {}
    for event in db_events:
        if event.event_type != "turn.started":
            continue
        sender_turn_id = event.payload.get("a2a_sender_turn_id")
        if sender_turn_id:
            a2a_turns.setdefault(sender_turn_id, []).append(
                event.payload.get("turn_id", "")
            )

    replies_by_turn: dict[str, dict] = {}
    for event in db_events:
        if event.event_type == "a2a.reply":
            turn_id = event.payload.get("turn_id", "")
            replies_by_turn[turn_id] = {
                "reply_text": event.payload.get("reply_text"),
                "reply_message_id": event.payload.get("reply_message_id"),
                "truncated": event.payload.get("truncated", False),
                "failed": False,
            }
        elif event.event_type == "a2a.reply.failed":
            turn_id = event.payload.get("turn_id", "")
            replies_by_turn[turn_id] = {
                "reply_text": event.payload.get("fallback_reply_text", ""),
                "failed": True,
                "reason": event.payload.get("reason", ""),
            }

    consumed: dict[str, int] = {}

    records = []
    for event in db_events:
        if event.event_type != "a2a.invoke":
            continue
        payload = event.payload
        created_at = (
            event.created_at.isoformat()
            if isinstance(event.created_at, datetime)
            else event.created_at
        )
        sender_turn_id = payload.get("sender_turn_id", "")
        idx = consumed.get(sender_turn_id, 0)
        target_turns = a2a_turns.get(sender_turn_id, [])
        target_turn_id: Optional[str] = target_turns[idx] if idx < len(target_turns) else None
        consumed[sender_turn_id] = idx + 1
        reply = replies_by_turn.get(target_turn_id or "", {})
        record = {
            "event_id": event.id,
            "sender_agent_id": payload.get("sender_agent_id"),
            "sender_turn_id": sender_turn_id,
            "target_agent_id": payload.get("target_agent_id"),
            "target_turn_id": target_turn_id,
            "purpose": payload.get("purpose", ""),
            "mode": payload.get("mode", "fire_and_forget"),
            "queued_position": payload.get("queued_position"),
            "created_at": created_at,
            "reply_text": reply.get("reply_text"),
            "reply_failed": reply.get("failed", False),
        }
        records.append(record)
    return records


def _derive_usage_summary(db_events: list) -> dict:
    usage_events = [e for e in db_events if e.event_type == "turn.usage"]
    if not usage_events:
        return {"mission_total": None, "by_agent": [], "by_provider": [], "by_turn": []}

    _FIELDS = ("input_tokens", "cached_input_tokens", "cache_creation_input_tokens",
               "output_tokens", "reasoning_output_tokens")

    def _extract(payload: dict) -> dict:
        u = payload.get("usage") or {}
        rec = {f: u.get(f, 0) for f in _FIELDS}
        rec["total_cost_usd"] = u.get("total_cost_usd")
        rec["duration_ms"] = u.get("duration_ms", 0)
        return rec

    def _sum(bucket: list) -> dict:
        out = {f: 0 for f in _FIELDS}
        out["duration_ms"] = 0
        cost_parts = [b["total_cost_usd"] for b in bucket if b["total_cost_usd"] is not None]
        out["total_cost_usd"] = sum(cost_parts) if cost_parts else None
        for b in bucket:
            for f in _FIELDS:
                out[f] += b[f]
            out["duration_ms"] += b["duration_ms"]
        return out

    per_turn = []
    agent_buckets: dict = {}
    provider_buckets: dict = {}
    all_recs = []

    for event in usage_events:
        p = event.payload or {}
        rec = _extract(p)
        agent_id = p.get("agent_id", "unknown")
        provider_id = p.get("provider_id", "unknown")
        turn_id = p.get("turn_id", "unknown")

        all_recs.append(rec)
        per_turn.append({"turn_id": turn_id, "agent_id": agent_id, "provider_id": provider_id, **rec})
        agent_buckets.setdefault(agent_id, []).append(rec)
        provider_buckets.setdefault(provider_id, []).append(rec)

    by_agent = [{"agent_id": aid, **_sum(recs)} for aid, recs in agent_buckets.items()]
    by_provider = [{"provider_id": pid, **_sum(recs)} for pid, recs in provider_buckets.items()]

    return {
        "mission_total": _sum(all_recs),
        "by_agent": by_agent,
        "by_provider": by_provider,
        "by_turn": per_turn,
    }


def _derive_thoughts(db_events) -> list[dict[str, object]]:
    """Derive agent.thought records from agent.thought events."""
    thoughts = []
    for event in db_events:
        if event.event_type != "agent.thought":
            continue
        payload = event.payload
        created_at = (
            event.created_at.isoformat()
            if isinstance(event.created_at, datetime)
            else event.created_at
        )
        record = {
            "id": event.id,
            "mission_id": event.mission_id,
            "agent_id": payload.get("agent_id"),
            "turn_id": payload.get("turn_id"),
            "provider_id": payload.get("provider_id"),
            "kind": payload.get("kind"),
            "content": payload.get("content", ""),
            "tags": payload.get("tags", []),
            "created_at": created_at,
        }
        thoughts.append(record)
    return thoughts


def build_mission_detail(repo: Repository, settings: Settings, mission_id: str) -> dict[str, object]:
    mission = repo.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")

    messages = repo.list_messages(mission_id)
    messages_by_id = {message.id: message for message in messages}
    db_events = repo.list_events_after(mission_id, 0)
    serialized_events = []
    serialized_message_ids = set()

    for index, event in enumerate(db_events):
        if event.event_type == "message":
            message_id = event.payload.get("id")
            message = messages_by_id.get(message_id)
            if message is None:
                continue
            serialized_events.append(_message_event(message, sort_key=(index, 0)))
            serialized_message_ids.add(message.id)
            continue
        serialized_events.append(_raw_event(event, sort_key=(index, 0)))

    missing_messages = [
        message for message in messages if message.id not in serialized_message_ids
    ]
    missing_messages.sort(key=lambda message: message.created_at)
    for index, message in enumerate(missing_messages):
        serialized_events.append(_message_event(message, sort_key=(len(db_events), index)))

    serialized_events.sort(key=lambda item: item["_sort_key"])
    events = [_strip_sort_key(event) for event in serialized_events]
    message_events = [
        _strip_sort_key(_message_event(message))
        for message in sorted(messages, key=lambda item: item.created_at)
    ]

    result = {
        "mission": _mission_payload(mission),
        "messages": message_events,
        "events": events,
        "artifacts": _artifact_records(settings, mission_id, db_events),
        "turns": _derive_turns(db_events),
        "tool_events": _derive_tool_events(db_events),
        "interrupts": _derive_interrupts(db_events),
        "a2a_invocations": _derive_a2a_invocations(db_events),
        "usage_summary": _derive_usage_summary(db_events),
        "thoughts": _derive_thoughts(db_events),
    }
    live_partial = get_live_progress_registry().snapshot_for_mission(mission_id)
    if live_partial:
        result["live_partial"] = live_partial
    return result
