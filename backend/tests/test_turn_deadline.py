import json
import subprocess
import time

import pytest

from backend.app.adapters.turn_deadline import ToolResultDeadline, is_tool_return, is_hub_work_result
from backend.app.adapters.stream_parsers import StreamLineParser
from backend.app.adapters.mcp_subprocess import _wait_for_process_with_activity, ProcessWaitReason


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def test_five_minute_windows_renew_ten_minutes_without_reusing_old_returns():
    clock = Clock()
    deadline = ToolResultDeadline(600, clock=clock)
    clock.now = 60
    deadline.record_result('read1')
    clock.now = 299
    assert deadline.check() is None
    clock.now = 300
    first = deadline.check()
    assert first['deadline_at'] == 900
    clock.now = 600
    assert deadline.check() is None
    assert deadline.deadline == 900
    clock.now = 601
    deadline.record_result('test1')
    clock.now = 900
    assert deadline.check()['deadline_at'] == 1500
    clock.now = 1200
    assert deadline.check() is None
    clock.now = 1500
    assert deadline.check() is None
    assert deadline.remaining() == 0
    assert deadline.extensions == 2


def test_progress_at_the_original_deadline_is_checked_before_expiry():
    clock = Clock()
    deadline = ToolResultDeadline(600, clock=clock)
    clock.now = 300
    assert deadline.check() is None
    clock.now = 599
    deadline.record_result('late_test')
    clock.now = 600.01  # Normal timer/poll scheduling drift.
    assert deadline.check()['deadline_at'] == 1200
    assert deadline.remaining() > 599


def test_silence_duplicate_returns_and_late_returns_cannot_keep_a_turn_alive():
    clock = Clock()
    deadline = ToolResultDeadline(600, clock=clock)
    clock.now = 20
    deadline.record_result('read1')
    clock.now = 300
    assert deadline.check()['new_tool_results'] == 1
    clock.now = 550
    deadline.record_result('read1')
    clock.now = 600
    assert deadline.check() is None
    clock.now = 901
    deadline.record_result('after_expiry')
    assert deadline.check() is None
    assert deadline.deadline == 900


def test_missed_checks_do_not_grant_an_unbounded_lease_after_a_long_pause():
    clock = Clock()
    deadline = ToolResultDeadline(600, clock=clock)
    clock.now = 20
    deadline.record_result('read1')
    clock.now = 2000
    deadline.check()
    assert deadline.deadline == 900
    assert deadline.remaining() == 0


@pytest.mark.parametrize('provider,frames,expected', [
    ('claude', [
        {'type': 'system', 'subtype': 'init', 'session_id': 's'},
        {'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': 0,
          'content_block': {'type': 'tool_use', 'id': 'r1', 'name': 'Bash', 'input': {}}}},
        {'type': 'stream_event', 'event': {'type': 'content_block_stop', 'index': 0}},
        {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'r1', 'content': ''}]}},
    ], [False, False, False, True]),
    ('codex', [
        {'type': 'item.started', 'item': {'type': 'command_execution', 'id': 'c1', 'command': 'test'}},
        {'type': 'item.completed', 'item': {'type': 'command_execution', 'id': 'c1', 'command': 'test', 'exit_code': 1}},
    ], [False, True]),
    ('antigravity', [
        {'event': 'step_update', 'step_update': {'step_type': 'tool', 'step_index': 1, 'state': 'ACTIVE', 'tool_name': 'read_file'}},
        {'event': 'step_update', 'step_update': {'step_type': 'tool', 'step_index': 1, 'state': 'DONE', 'tool_name': 'read_file'}},
    ], [False, True]),
])
def test_only_real_tool_returns_qualify(provider, frames, expected):
    parser = StreamLineParser(provider)
    observed = []
    for frame in frames:
        event = parser.parse(json.dumps(frame))
        observed.append(bool(event and is_tool_return(event)))
    assert observed == expected


def test_plans_and_tool_starts_do_not_qualify_as_hub_progress():
    from types import SimpleNamespace
    for kind, tool, qualifies in [('tool.call', 'read_artifact', False),
                                  ('tool.result', 'log_thought', False),
                                  ('tool.result', 'publish_message', False),
                                  ('tool.result', 'read_artifact', True),
                                  ('tool.error', 'read_artifact', True)]:
        record = SimpleNamespace(event_type=kind, payload={'turn_id': 't1', 'tool_name': tool})
        assert is_hub_work_result(record, 't1') is qualifies
        assert not is_hub_work_result(record, 'another_turn')


def test_external_tool_results_count_but_hub_self_reports_do_not():
    from backend.app.adapters.stream_events import StreamEvent, StreamEventType
    def result(name, args=None):
        return StreamEvent(StreamEventType.TOOL_ATTEMPT, tool_name=name, tool_args=args,
                           tool_phase='completed', tool_ok=True)
    assert is_tool_return(result('mcp:files:read_file'))
    assert not is_tool_return(result('mcp:yiren_hub:log_thought'))
    assert not is_tool_return(result('mcp__yiren_hub__publish_message'))
    assert not is_tool_return(result('call_mcp_tool', {'ServerName': 'yiren_hub'}))
    assert is_tool_return(result('call_mcp_tool', {'ServerName': 'file_service'}))


def test_process_wait_survives_original_limit_and_then_stops_without_new_results():
    clock = Clock()
    deadline = ToolResultDeadline(600, clock=clock)
    checks = []
    refreshes = set()

    def refresh():
        if clock.now in (299, 599) and clock.now not in refreshes:
            deadline.record_result(clock.now)
            refreshes.add(clock.now)

    class Process:
        def wait(self, timeout):
            clock.now += timeout
            raise subprocess.TimeoutExpired('fake', timeout)

    # The legacy waiter also checks the wall clock for its optional idle policy.
    from unittest.mock import patch
    with patch('backend.app.adapters.mcp_subprocess.time.time', clock):
        reason = _wait_for_process_with_activity(Process(), hard_timeout=600, idle_timeout=0,
            last_activity_at=lambda: 0, refresh_activity=refresh,
            progress_deadline=deadline, on_extension=checks.append)
    assert reason is ProcessWaitReason.HARD_TIMEOUT
    assert clock.now == 1200
    assert len(checks) == 2


def test_grant_renewal_keeps_tools_authorized_but_never_revives_fenced_turns(repository, temp_settings):
    from backend.app.domain.models import Mission, MissionStatus
    from backend.app.storage.execution import ExecutionLedger
    from backend.app.mcp.tools import MCPToolError
    repository.create_mission(Mission(id='m_renew', title='renew', goal='renew', status=MissionStatus.RUNNING))
    ledger = ExecutionLedger(repository)
    ledger.register('worker')
    token, _ = ledger.begin('worker', 'm_renew', 'wangye', 't1', 'antigravity', timeout=30)
    ledger.renew('worker', token, timeout=600)
    with repository._transaction() as db:
        grant = ledger._grant(db, 'worker', token)
    assert grant['expires_at'] > time.time() + 590
    ledger.revoke('worker', 'test')
    with pytest.raises(MCPToolError, match='expired'):
        ledger.renew('worker', token, timeout=600)
