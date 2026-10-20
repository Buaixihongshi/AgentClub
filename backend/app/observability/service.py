"""Observability payload: one answer for the UI, built from turns.

The UI should not stitch together five endpoints to answer "what happened and
was it any good". This module assembles turns, checks, coverage and spend into
one dict, scoped either to a mission or to the most recent missions, and the
API layer simply serves it.

Coverage is reported next to the checks on purpose: `n/a` on a check means
"the trace could not settle it", and coverage explains why. A page that shows
failures without showing how much it can see is a page that teaches people to
distrust it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from backend.app.domain.models import Mission
from backend.app.observability import checks as checks_module
from backend.app.observability.turns import Turn, build_turns
from backend.app.storage.repository import Repository

DEFAULT_TURN_LIMIT = 50
DEFAULT_MISSION_LIMIT = 20


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sort_key(turn: Turn) -> str:
    return turn.started_at or ""


def agent_names() -> dict[str, str]:
    """id -> display name. Best effort: a registry problem must not break the page."""
    try:
        from backend.app.domain.agent_registry import get_agent_registry

        return {agent.id: agent.display_name for agent in get_agent_registry().all()}
    except Exception:  # noqa: BLE001 - display names are cosmetic
        return {}


def collect_turns(
    repository: Repository,
    *,
    mission_id: Optional[str] = None,
    mission_limit: int = DEFAULT_MISSION_LIMIT,
) -> list[Turn]:
    """Turns for one mission, or for the most recent missions, oldest first.

    Turns are graded as they are built: `turn.checks` is filled here, so every
    reader of the payload sees the same verdicts.
    """
    if mission_id:
        mission = repository.get_mission(mission_id)
        missions: list[Mission] = [mission] if mission else []
    else:
        missions = sorted(repository.list_missions(), key=lambda m: m.created_at, reverse=True)
        missions = missions[:max(mission_limit, 0)]

    turns: list[Turn] = []
    for mission in missions:
        events = repository.list_events_after(mission.id, 0)
        messages = repository.list_messages(mission.id)
        mission_turns = build_turns(events, messages, mission)
        for turn in mission_turns:
            turn.checks = checks_module.run_checks(turn)
        turns.extend(mission_turns)
    return turns


def _coverage(turns: list[Turn]) -> dict:
    tool_calls = sum(len(t.tools) for t in turns)
    tool_outputs = sum(1 for t in turns for step in t.tools if step.output.strip())
    return {
        "turns": len(turns),
        "turns_with_reply": sum(1 for t in turns if t.reply.strip()),
        "turns_native_visible": sum(1 for t in turns if t.native_visible),
        "native_visible_rate": round(sum(1 for t in turns if t.native_visible) / len(turns), 3) if turns else None,
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
        "tool_output_rate": round(tool_outputs / tool_calls, 3) if tool_calls else None,
        "turns_cost_known": sum(1 for t in turns if t.cost_known),
        "cost_known_rate": round(sum(1 for t in turns if t.cost_known) / len(turns), 3) if turns else None,
    }


def _spend(turns: list[Turn]) -> dict:
    by_provider: dict[str, dict[str, float]] = {}
    total = reported = estimated = 0.0
    unknown = 0
    for turn in turns:
        provider = turn.provider_id or "unknown"
        bucket = by_provider.setdefault(provider, {"usd": 0.0, "turns": 0, "unknown": 0})
        bucket["turns"] += 1
        if turn.cost_usd is None:
            unknown += 1
            bucket["unknown"] += 1
            continue
        total += turn.cost_usd
        bucket["usd"] += turn.cost_usd
        if turn.cost_source == "provider":
            reported += turn.cost_usd
        else:
            estimated += turn.cost_usd
    return {
        "total_usd": round(total, 6),
        "reported_usd": round(reported, 6),
        "estimated_usd": round(estimated, 6),
        "unknown_turns": unknown,
        "by_provider": [
            {"provider_id": provider, "usd": round(row["usd"], 6), "turns": int(row["turns"]),
             "unknown": int(row["unknown"])}
            for provider, row in sorted(by_provider.items(), key=lambda kv: -kv[1]["usd"])
        ],
    }


def build_payload(
    repository: Repository,
    *,
    mission_id: Optional[str] = None,
    limit: int = DEFAULT_TURN_LIMIT,
    mission_limit: int = DEFAULT_MISSION_LIMIT,
    agent_names_map: Optional[dict[str, str]] = None,
) -> dict:
    """Everything the observability panel draws, in one response.

    `limit` caps turns after sorting newest-first; the checks summary still
    covers every turn in scope, so a truncated list never inflates a pass rate.
    """
    names = agent_names_map if agent_names_map is not None else agent_names()
    turns = collect_turns(repository, mission_id=mission_id, mission_limit=mission_limit)
    all_checks = checks_module.summarize(turns)

    ordered = sorted(turns, key=_sort_key, reverse=True)
    visible = ordered[: max(limit, 0)] if limit else ordered
    mission = repository.get_mission(mission_id) if mission_id else None

    return {
        "generated_at": _iso_now(),
        "scope": {
            "mission_id": mission_id,
            "mission_title": (mission.title or mission.goal) if mission else None,
            "missions": len({turn.mission_id for turn in turns}),
            "turns": len(turns),
            "shown": len(visible),
        },
        "summary": {
            "checks": all_checks,
            "coverage": _coverage(turns),
            "spend": _spend(turns),
        },
        "turns": [turn.to_dict(agent_name=names.get(turn.agent_id, "")) for turn in visible],
    }


def turn_detail(repository: Repository, turn_id: str) -> Optional[dict]:
    """One turn with its steps, checks and reply, found by id across missions."""
    mission_id = repository.mission_id_for_turn(turn_id)
    if not mission_id:
        return None
    mission = repository.get_mission(mission_id)
    if mission is None:
        return None
    turns = build_turns(
        repository.list_events_after(mission.id, 0),
        repository.list_messages(mission.id),
        mission,
    )
    turn = next((candidate for candidate in turns if candidate.turn_id == turn_id), None)
    if turn is None:
        return None
    turn.checks = checks_module.run_checks(turn)
    names = agent_names()
    return {
        "mission": {"id": mission.id, "title": mission.title or mission.goal},
        "turn": turn.to_dict(agent_name=names.get(turn.agent_id, "")),
    }
