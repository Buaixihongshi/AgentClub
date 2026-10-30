"""Discussion parts of mission creation: control payloads and discussion config."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, StrictInt

from backend.app.domain.errors import DiscussionError
from backend.app.domain.models import DiscussionConfig


class DiscussionControlRequest(BaseModel):
    action: Literal['finish', 'stop', 'select_synthesizer', 'retry', 'record_decision', 'restart_with_input', 'defer_input', 'save_knowledge_candidate']
    request_id: str = Field(min_length=1, max_length=128)
    summary_agent_id: Optional[str] = None
    expected_revision: Optional[StrictInt] = None
    agent_id: Optional[str] = None
    turn_id: Optional[str] = None
    decision_id: Optional[str] = None
    option_id: Optional[str] = None
    input_ids: Optional[list[str]] = None
    goal: Optional[str] = None


def build_config(repo, settings, payload, registry, valid_ids):
    """Validated DiscussionConfig for a roundtable or V2 request."""
    participants = payload.discussion.participant_ids
    if participants is None and payload.skill == 'discuss':
        # Discuss runs with 2–3 voices; extra user roles join only
        # when the request names them explicitly.
        participants = registry.ids()[:3]
        if not 2 <= len(participants) <= 3:
            raise DiscussionError('Discuss skill requires 2–3 project agents or explicit participant_ids', 422)
    if any(p not in valid_ids for p in participants):
        raise DiscussionError('Unknown discussion participant', 422)
    limit = payload.discussion.max_turns or len(participants) * 2 + 1
    if payload.discussion.protocol == 'roundtable_v1' and limit > settings.roundtable_max_turns:
        raise DiscussionError('Discussion exceeds server roundtable_max_turns', 422)
    config = DiscussionConfig(participant_ids=participants, max_turns=limit)
    if payload.discussion.protocol == 'deliberation_v2':
        from backend.app.orchestration.runner import _apply_agent_overrides
        profiles = _apply_agent_overrides(registry.all(), settings.agent_overrides)
        routes = {a.id: [a.default_provider_id, *a.fallback_provider_ids] for a in profiles if a.id in participants}
        if settings.use_fake_cli:
            routes = {agent: ['fake'] for agent in participants}
        limit = 2 * len(participants) + len(participants) * payload.discussion.max_exchange_rounds + 1
        if limit > settings.deliberation_max_turns:
            raise DiscussionError('Discussion exceeds deliberation_max_turns', 422)
        if not settings.use_fake_cli:
            from backend.app.extensions.discussion.deliberation_cli import isolation_supported
            from backend.app.config.providers import get_cli_provider, ProviderNotFoundError
            from pathlib import Path
            for agent, candidates in routes.items():
                eligible = []
                for pid in dict.fromkeys(candidates):
                    if pid in settings.api_connections:
                        if settings.api_connections[pid].get('enabled'):
                            eligible.append(pid)
                        continue
                    try:
                        provider = get_cli_provider(settings.config_dir, pid, overrides=settings.provider_overrides)
                    except ProviderNotFoundError:
                        continue
                    if provider.enabled and (pid == 'fake' or (provider.mcp_enabled and Path(provider.command).name in {'codex', 'claude', 'agy'})):
                        eligible.append(pid)
                if not eligible:
                    raise DiscussionError(f'isolation_not_supported: no enabled isolated provider for {agent}', 422)
                if candidates[0] not in eligible:
                    raise DiscussionError(f'configuration: primary provider unavailable for {agent}', 422)
                # Never promote an API fallback to primary by filtering
                # the configured route: that would bypass export consent.
                routes[agent] = list(dict.fromkeys(candidates))
            if any(pid != 'fake' and pid not in settings.api_connections for route in routes.values() for pid in route) and not isolation_supported():
                raise DiscussionError('isolation_not_supported: V2 requires macOS sandbox-exec', 422)
        config = DiscussionConfig(participant_ids=participants, max_turns=limit, protocol='deliberation_v2',
            max_exchange_rounds=payload.discussion.max_exchange_rounds, provider_routes=routes,
            profiles=[a.model_dump(mode='json') for a in profiles if a.id in participants])
        if payload.skill == 'discuss':
            from backend.app.domain.project_skills import load_discuss_skill
            try:
                config.skill_instructions, config.skill_hash = load_discuss_skill()
            except (OSError, ValueError) as exc:
                raise DiscussionError('Project skill discuss is unavailable; check skills/discuss/SKILL.md', 503) from exc
    return config
