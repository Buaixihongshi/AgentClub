"""Sandboxed discussion and work turns keep complete records (real sandbox-exec, offline CLI)."""
import json
import sys
from pathlib import Path

import pytest

from backend.app.adapters.deliberation_cli import DeliberationCLIAdapter, isolation_supported
from backend.app.adapters.turn_record import read_records
from backend.app.domain.models import TurnStatus
from backend.app.mcp.tools import MCPTools
from backend.app.orchestration.deliberation import Deliberation
from backend.tests.test_deliberation import create
from backend.tests.test_deliberation_isolation import FAKE_CODEX


pytestmark = pytest.mark.skipif(not isolation_supported(), reason='macOS sandbox-exec integration')

NATIVE_STREAM = '''
print(json.dumps({'type':'thread.started','thread_id':'th_sandboxed'}),flush=True)
print(json.dumps({'type':'item.started','item':{'id':'i1','type':'command_execution','command':'ls'}}),flush=True)
print(json.dumps({'type':'item.completed','item':{'id':'i1','type':'command_execution','command':'ls','aggregated_output':'a'*3000,'exit_code':0,'status':'completed'}}),flush=True)
import sys
print('sandboxed stderr line',file=sys.stderr,flush=True)
'''


def _install(tmp_path, source):
    install = tmp_path / 'cli'
    install.mkdir()
    executable = install / 'codex'
    executable.write_text(f'#!{Path(sys.executable).resolve()}\n' + source)
    executable.chmod(0o700)
    config = tmp_path / 'providers'
    config.mkdir()
    (config / 'cli-providers.json').write_text(json.dumps([{
        'id': 'codex', 'label': 'offline test CLI', 'command': str(executable),
        'args': ['exec', '--json'], 'enabled': True, 'mcp_enabled': True,
    }]))
    return config


def test_discussion_turn_record_is_complete_but_activity_stays_private(repository, temp_settings, tmp_path,
                                                                       monkeypatch):
    config = _install(tmp_path, FAKE_CODEX.replace('context=call(1', NATIVE_STREAM + 'context=call(1', 1))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'empty-home'))
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    lease = policy.claim_batch(mission.id, temp_settings.artifact_root)[0]
    auth = policy.authorize(mission.id, lease['turn_id'], 'codex')
    tools = MCPTools(repository, temp_settings.artifact_root, lease_token=auth['token'])
    outcome = DeliberationCLIAdapter(config, 15, {}).run_turn(
        mission.id, lease['agent_id'], lease['turn_id'], 'codex', 'offline discussion prompt', tools)
    assert policy.state(repository.get_mission(mission.id))['leases'][lease['turn_id']]['status'] == 'submitted', \
        outcome.summary

    [record] = read_records(temp_settings.database_path, mission.id, lease['turn_id'])
    entries = record['entries']
    assert record['route'] == 'discussion' and record['finished']
    environment = next(e for e in entries if e['kind'] == 'environment')
    assert environment['isolation'] == 'macos-sandbox+host-mcp-bridge'
    assert environment['argv'][:2] == ['/usr/bin/sandbox-exec', '-f'] and environment['argv'][-1] == '<prompt>'
    assert '(deny default)' in environment['sandbox_profile']
    assert next(e for e in entries if e['kind'] == 'input')['prompt'] == 'offline discussion prompt'
    stdout = [e['line'] for e in entries if e['kind'] == 'stdout']
    assert any('"turn.completed"' in line for line in stdout)
    assert any(len(json.loads(line).get('item', {}).get('aggregated_output', '')) == 3000
               for line in stdout if line.startswith('{'))
    assert any(e['kind'] == 'stderr' and e['line'] == 'sandboxed stderr line' for e in entries)
    finished = entries[-1]
    assert finished['kind'] == 'exit' and finished['accepted'] is True and finished['session_id'] == 'th_sandboxed'
    # Unreleased discussion work never reaches the mission trace.
    kinds = {e.event_type for e in repository.list_events_after(mission.id, 0)}
    assert not kinds & {'provider.activity', 'provider.session'}


def test_work_turn_streams_native_activity_live_and_records_it(repository, temp_settings, tmp_path, monkeypatch):
    from backend.tests.test_adaptive_work import create as create_work, run as run_work
    from backend.app.domain.models import Conversation
    from backend.app.work.adaptive import AdaptiveWork
    import subprocess

    root = tmp_path / 'project'
    (root / 'docs').mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-qm', 'initial'], check=True)
    repository.create_conversation(Conversation(id='c_work', title='自主工作'))
    work = AdaptiveWork(repository, root, check_timeout=1)
    create_work(work)
    mission = run_work(work)
    context = work.execution_context(mission.id)
    assert context
    config = _install(tmp_path, 'import json\n' + NATIVE_STREAM)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'empty-home'))
    tools = MCPTools(repository, temp_settings.artifact_root)
    outcome = DeliberationCLIAdapter(config, 15, {}, work_context=context).run_turn(
        mission.id, mission.target_agent_id, 't_work', 'codex', 'offline work prompt', tools)
    # No structured submission: the attempt is honestly blocked, yet fully recorded.
    assert outcome.status == TurnStatus.BLOCKED

    events = repository.list_events_after(mission.id, 0)
    sessions = [e.payload for e in events if e.event_type == 'provider.session']
    assert sessions == [{'turn_id': 't_work', 'agent_id': mission.target_agent_id, 'provider_id': 'codex',
                         'session_id': 'th_sandboxed', 'model': None}]
    activity = [e.payload for e in events if e.event_type == 'provider.activity']
    assert [a['phase'] for a in activity] == ['started', 'completed'] and activity[0]['tool_name'] == 'shell'
    [record] = read_records(temp_settings.database_path, mission.id, 't_work')
    assert record['route'] == 'work'
    environment = next(e for e in record['entries'] if e['kind'] == 'environment')
    assert environment['cwd'] == str(Path(context['workspace_root']))
    assert record['entries'][-1]['accepted'] is False and record['entries'][-1]['exit_code'] == 0
