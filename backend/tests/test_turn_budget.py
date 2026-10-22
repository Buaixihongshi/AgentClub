from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.turn_budget import budgeted_turn, claim_attempt, remaining_timeout
from backend.app.adapters.turn_deadline import ToolResultDeadline
from backend.app.domain.models import Mission, TurnStatus
from backend.app.mcp.tools import MCPTools
from backend.app.orchestration.runner import MissionRunner


@pytest.mark.parametrize('elapsed,expected', [(0, ['codex', 'gemini']), (301, ['codex'])])
def test_chat_shared_budget(repository, temp_settings, monkeypatch, elapsed, expected):
    now = [1000.0]
    monkeypatch.setattr('backend.app.adapters.turn_budget.time.monotonic', lambda: now[0])
    temp_settings.cli_timeout = 300
    mission = Mission(id='budget', title='budget', goal='test', status='running')
    repository.create_mission(mission)
    calls, remaining = [], []

    def run(*args):
        calls.append(args[3])
        remaining.append(remaining_timeout(600))
        now[0] += elapsed or 40
        return TurnOutcome(status=TurnStatus.BLOCKED, failure_code='network', retryable=True)

    runner = MissionRunner(repository, temp_settings, Mock(run_turn=run))
    outcome = runner._run_turn_with_fallbacks(mission, 'chulan', 'budget-turn',
        ['codex', 'codex', 'gemini', 'claude'], 'test', MCPTools(repository, temp_settings.artifact_root))
    assert calls == expected
    assert remaining == ([300, 260] if not elapsed else [300])
    assert outcome.failure_code == 'budget_exhausted'
    assert remaining_timeout(600) == 600


def test_budget_resets_on_exception_and_isolates_workers():
    @budgeted_turn(300)
    def work():
        assert claim_attempt()
        assert claim_attempt()
        assert not claim_attempt()
        raise RuntimeError('test')

    def worker(_):
        with pytest.raises(RuntimeError):
            work()
        assert claim_attempt()
        assert remaining_timeout(600) == 600

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(worker, range(6)))


def test_tool_activity_cannot_extend_shared_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr('backend.app.adapters.turn_budget.time.monotonic', lambda: now[0])

    @budgeted_turn(10)
    def work():
        progress = ToolResultDeadline(10, check_interval=5, clock=lambda: now[0])
        now[0] = 4
        progress.record_result('tool')
        now[0] = 5
        assert progress.check() is None
        assert progress.remaining() == 5
        now[0] = 11
        assert progress.remaining() == 0
    work()


def test_api_budget_expired_before_dns(monkeypatch):
    from backend.app.adapters.api_transport import APIError, request
    resolve = Mock()
    monkeypatch.setattr('backend.app.adapters.api_transport.resolve_endpoint', resolve)
    now = [0.0]
    monkeypatch.setattr('backend.app.adapters.turn_budget.time.monotonic', lambda: now[0])

    @budgeted_turn(10)
    def work():
        now[0] = 11
        with pytest.raises(APIError) as error:
            with request({'base_url': 'https://example.com', 'protocol': 'openai_chat'},
                         'secret', 'GET', '/models'):
                pytest.fail('expired request started')
        assert error.value.code == 'timeout'
        resolve.assert_not_called()
    work()
