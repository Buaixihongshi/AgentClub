"""Shared explicit-route and context-export guards."""
import hashlib
import json


def endpoint_binding(connection):
    return hashlib.sha256(json.dumps([connection['base_url'], connection['protocol'],
                                     connection.get('allow_private', False)]).encode()).hexdigest()


def context_approved(settings, agent_id, provider_id):
    connection = settings.api_connections.get(provider_id)
    if connection is None:
        return not provider_id.startswith('api-')
    override = settings.agent_overrides.get(agent_id)
    return bool(override and override.fallback_context_approved and
                override.fallback_context_targets.get(provider_id) == endpoint_binding(connection))


def may_retry(outcome, events=()):
    if outcome.retryable is False or outcome.output_started:
        return False
    if outcome.failure_code in {'authentication', 'configuration', 'invalid_arguments', 'stopped', 'endpoint_policy'}:
        return False
    # Legacy CLI adapters predate typed failure codes; classify known permanent
    # diagnostics and conservatively stop if any tool or output was observed.
    if any(word in (outcome.summary or '').lower() for word in ('401', '403', 'authentication', 'invalid argument', 'configuration', 'disabled')):
        return False
    return not any(e.event_type in {'tool.call', 'provider.activity', 'provider.output_started'} for e in events)
