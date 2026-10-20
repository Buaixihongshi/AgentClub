"""Actual sandbox/process/stdio checks, without a paid provider call."""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from backend.app.adapters.deliberation_cli import DeliberationCLIAdapter, isolation_supported, sandbox_profile
from backend.app.orchestration.deliberation import Deliberation
from backend.app.mcp.tools import MCPTools
from backend.tests.test_deliberation import create


pytestmark = pytest.mark.skipif(not isolation_supported(), reason='macOS sandbox-exec integration')


def test_sandbox_blocks_peer_files_http_and_process_inspection(tmp_path):
    peer = tmp_path / 'peer-opinion.txt'
    marker = 'SEALED_PEER_CANARY'
    peer.write_text(marker)
    with tempfile.TemporaryDirectory(prefix='acv2-test-', dir='/tmp') as temp:
        work = Path(temp).resolve()
        (work / 'own.txt').write_text('OWN_INPUT')
        profile = work / 'profile.sb'
        profile.write_text(sandbox_profile(work, str(Path(sys.executable).resolve())))
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        code = f'''from pathlib import Path
import socket,subprocess
assert Path('own.txt').read_text() == 'OWN_INPUT'
try:
    Path({str(peer)!r}).read_text()
    raise AssertionError('peer file leaked')
except PermissionError: pass
s=socket.socket()
try:
    s.connect(('127.0.0.1',{port}))
    raise AssertionError('local API reachable')
except PermissionError: pass
try:
    p=subprocess.run(['/bin/ps','-A','-o','command'],capture_output=True)
    assert p.returncode != 0
except PermissionError: pass
print('isolated')
'''
        try:
            result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), str(Path(sys.executable).resolve()),
                                     '-I', '-S', '-c', code], cwd=work, capture_output=True, text=True, timeout=10)
        finally:
            listener.close()
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'isolated'


FAKE_CODEX = '''import json,os,subprocess,tomllib
from pathlib import Path
config=tomllib.loads((Path(os.environ['CODEX_HOME'])/'config.toml').read_text())
server=config['mcp_servers']['yiren_hub']
assert 'env' not in server
assert not any(k.startswith('YIREN_') for k in os.environ)
p=subprocess.Popen([server['command'],*server['args']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def call(n,name,arguments):
    p.stdin.write(json.dumps({'jsonrpc':'2.0','id':n,'method':'tools/call','params':{'name':name,'arguments':arguments}})+'\\n');p.stdin.flush()
    return json.loads(p.stdout.readline())['result']
context=call(1,'read_context',{})
assert not context.get('isError'),context
view=json.loads(context['content'][0]['text'])
if view['phase']=='independent':
    assert not view['results']
    body={'kind':'opinion','position_summary':'来自真正受限进程的提交','claims':[{'key':'a','decision_key':'next','statement':'先验证','recommendation':'验证'}]}
elif view['phase']=='review':
    body={'kind':'review','draft_id':view['draft_id'],'no_findings':True}
else:
    refs=[c['id'] for r in view['results'] if r['kind']=='opinion' for c in r['data']['claims']]
    body={'kind':'draft','sections':[{'id':sid,'content':'保留各自依据和未知','source_ids':refs} for sid in ['recommendation','consensus','disagreements','pending','actions','sources']]}
result=call(2,'submit_discussion_result',{'result':body})
assert not result.get('isError'),result
p.stdin.close();p.wait(timeout=5)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':12,'output_tokens':5}}))
'''


@pytest.mark.parametrize('long_running', [False, True])
def test_isolated_cli_can_only_submit_via_its_host_owned_bridge(repository, temp_settings, tmp_path, monkeypatch, long_running):
    install = tmp_path / 'provider'
    install.mkdir()
    executable = install / 'codex'
    source = FAKE_CODEX
    if long_running:
        source = source.replace("if view['phase']=='independent':",
            "import time\nfor i in range(6):\n call(10+i,'read_context',{})\n time.sleep(0.25)\nif view['phase']=='independent':")
    executable.write_text(f'#!{Path(sys.executable).resolve()}\n' + source)
    executable.chmod(0o700)
    config = tmp_path / 'providers'
    config.mkdir()
    (config / 'cli-providers.json').write_text(json.dumps([{
        'id': 'codex', 'label': 'offline test CLI', 'command': str(executable),
        'args': ['exec', '--json'], 'enabled': True, 'mcp_enabled': True,
    }]))
    # Never copy the user's real authentication into this test.
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'empty-home'))
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    lease = policy.claim_batch(mission.id, temp_settings.artifact_root)[0]
    auth = policy.authorize(mission.id, lease['turn_id'], 'codex')
    tools = MCPTools(repository, temp_settings.artifact_root, lease_token=auth['token'])
    adapter = DeliberationCLIAdapter(config, 1.2 if long_running else 15, {}, progress_check_interval=0.2)
    outcome = adapter.run_turn(mission.id, lease['agent_id'], lease['turn_id'], 'codex', 'offline test', tools)
    actual = policy.state(repository.get_mission(mission.id))['leases'][lease['turn_id']]
    assert actual['status'] == 'submitted', outcome.summary
    assert policy.user_view(repository.get_mission(mission.id))['results'] == []
    assert not [m for m in repository.list_messages(mission.id) if m.role == 'agent']
    if long_running:
        assert any(e.event_type == 'turn.deadline_extended' for e in repository.list_events_after(mission.id, 0))


def test_entire_protocol_runs_through_real_isolated_processes(repository, temp_settings, tmp_path, monkeypatch):
    from backend.app.orchestration.runner import MissionRunner
    from backend.tests.test_deliberation import control
    install = tmp_path / 'cli'
    install.mkdir()
    executable = install / 'codex'
    executable.write_text(f'#!{Path(sys.executable).resolve()}\n' + FAKE_CODEX)
    executable.chmod(0o700)
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'cli-providers.json').write_text(json.dumps([
        {'id': pid, 'label': pid, 'command': str(executable), 'args': ['exec'], 'enabled': True, 'mcp_enabled': True}
        for pid in ['codex', 'claude', 'antigravity', 'fake']
    ]))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'empty-home'))
    mission = create(repository, temp_settings)
    isolated = DeliberationCLIAdapter(config, 15, {})
    class Adapter:
        run_turn = isolated.run_turn
    MissionRunner(repository, temp_settings, Adapter()).run(mission.id)
    policy = Deliberation(repository)
    assert policy.state(repository.get_mission(mission.id))['phase'] == 'awaiting_synthesizer'
    control(repository, temp_settings, mission.id, 'select_synthesizer', agent_id='chulan')
    MissionRunner(repository, temp_settings, Adapter()).run(mission.id)
    assert policy.state(repository.get_mission(mission.id))['phase'] == 'ended'
    assert len([e for e in repository.list_events_after(mission.id, 0) if e.event_type == 'discussion.provider_execution']) == 6


@pytest.mark.parametrize('provider,basename,args', [
    ('codex', 'codex', ['exec', '--json']),
    ('claude', 'claude', ['--output-format', 'stream-json', '-p']),
    ('antigravity', 'agy', ['--dangerously-skip-permissions', '-p']),
])
def test_each_provider_launch_shape_reaches_actual_stdio_bridge(repository, temp_settings, tmp_path, monkeypatch, provider, basename, args):
    install = tmp_path / 'cli'
    install.mkdir()
    executable = install / basename
    header = '''import json,os,subprocess,sys
from pathlib import Path
assert Path(os.environ['TMPDIR']).resolve() == Path.cwd()
assert Path(os.environ['CLAUDE_CODE_TMPDIR']).resolve() == Path.cwd()
'''
    if basename == 'codex':
        source = FAKE_CODEX
    elif basename == 'claude':
        source = header + '''assert sys.argv[-2:] == ['-p', 'offline test']
assert Path(os.environ['CLAUDE_CONFIG_DIR']).is_relative_to(Path.cwd())
config=json.loads(Path(sys.argv[sys.argv.index('--mcp-config')+1]).read_text())
server=config['mcpServers']['yiren_hub']
''' + "assert 'env' not in server" + FAKE_CODEX.split("assert 'env' not in server", 1)[1]
    else:
        source = header + '''assert sys.argv[-1] == '-p=offline test'
assert Path(os.environ['HOME']).is_relative_to(Path.cwd())
alias=Path.home()/'Library/Keychains/login.keychain-db'
assert alias.is_symlink() and alias.read_text() == 'fake-keychain'
config=json.loads((Path.home()/'.gemini/config/mcp_config.json').read_text())
server=config['mcpServers']['yiren_hub']
''' + "assert 'env' not in server" + FAKE_CODEX.split("assert 'env' not in server", 1)[1]
    executable.write_text(f'#!{Path(sys.executable).resolve()}\n' + source)
    executable.chmod(0o700)
    config = tmp_path / 'providers'
    config.mkdir()
    (config / 'cli-providers.json').write_text(json.dumps([{
        'id': provider, 'label': provider, 'command': str(executable), 'args': args,
        'enabled': True, 'mcp_enabled': True,
    }]))
    fake_home = tmp_path / 'empty-home'
    if basename == 'agy':
        keychain = fake_home / 'Library/Keychains/login.keychain-db'
        keychain.parent.mkdir(parents=True)
        keychain.write_text('fake-keychain')
        global_config = fake_home / '.gemini/config/mcp_config.json'
        global_config.parent.mkdir(parents=True)
        global_config.write_text('USER_CONFIG_MUST_NOT_CHANGE')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: fake_home))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'offline-test-only')
    mission = create(repository, temp_settings)
    policy = Deliberation(repository)
    lease = policy.claim_batch(mission.id, temp_settings.artifact_root)[0]
    auth = policy.authorize(mission.id, lease['turn_id'], provider)
    tools = MCPTools(repository, temp_settings.artifact_root, lease_token=auth['token'])
    outcome = DeliberationCLIAdapter(config, 10, {}).run_turn(mission.id, lease['agent_id'], lease['turn_id'], provider, 'offline test', tools)
    assert policy.state(repository.get_mission(mission.id))['leases'][lease['turn_id']]['status'] == 'submitted', outcome.summary
    assert outcome.failure_code is None
    if basename == 'agy':
        assert global_config.read_text() == 'USER_CONFIG_MUST_NOT_CHANGE'


def test_only_configured_local_model_proxy_port_is_reachable(tmp_path):
    provider = socket.socket()
    application = socket.socket()
    provider.bind(('127.0.0.1', 0)); provider.listen()
    application.bind(('127.0.0.1', 0)); application.listen()
    try:
        with tempfile.TemporaryDirectory(prefix='acv2-network-', dir='/tmp') as temp:
            work = Path(temp).resolve()
            profile = work / 'profile.sb'
            profile.write_text(sandbox_profile(work, str(Path(sys.executable).resolve()), loopback_ports=[provider.getsockname()[1]]))
            code = f'''import socket
s=socket.create_connection(('127.0.0.1',{provider.getsockname()[1]}),timeout=1);s.close()
try:
    socket.create_connection(('127.0.0.1',{application.getsockname()[1]}),timeout=1)
    raise AssertionError('application leaked')
except PermissionError: pass
'''
            result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), str(Path(sys.executable).resolve()), '-I', '-S', '-c', code],
                                    cwd=work, capture_output=True, text=True, timeout=5)
            assert result.returncode == 0, result.stderr
    finally:
        provider.close(); application.close()


@pytest.mark.skipif(os.environ.get('YIREN_CHECK_INSTALLED_CLI') != '1' or not shutil.which('codex'),
                    reason='opt-in installed Codex initialization, no model credentials')
def test_installed_codex_initializes_managed_preferences_without_user_credentials():
    with tempfile.TemporaryDirectory(prefix='acv2-codex-init-', dir='/tmp') as temp:
        work = Path(temp).resolve()
        (work / 'config.toml').write_text('model_provider="offline-probe"\n[model_providers.offline-probe]\n'
                                         'name="offline-probe"\nbase_url="http://127.0.0.1:9/v1"\nwire_api="responses"\n')
        profile = work / 'profile.sb'
        profile.write_text(sandbox_profile(work, 'codex'))
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'OPENAI_', 'YIREN_'))}
        env.update(CODEX_HOME=str(work), TMPDIR=str(work))
        proc = subprocess.Popen(['/usr/bin/sandbox-exec', '-f', str(profile), 'codex', 'exec', '--json',
                                 '--skip-git-repo-check', '--ephemeral', '--dangerously-bypass-approvals-and-sandbox', 'startup probe'],
                                cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=5)
        assert b'Failed to synchronize managed preferences' not in stderr
        assert b'"type":"thread.started"' in stdout, stderr.decode(errors='replace')[-1000:]
