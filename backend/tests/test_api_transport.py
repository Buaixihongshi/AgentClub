"""Repeatable protocol tests: actual local sockets, no cloud credentials/fake CLI."""
import json
import socket
import socketserver
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.app.adapters.api_transport import APIError, request, stream_text
from backend.app.adapters.api_catalog import catalog
from backend.app.adapters.api_text import APITextAdapter
from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.fallback_policy import context_approved, endpoint_binding
from backend.app.domain.models import Mission, TurnStatus
from backend.app.mcp.tools import MCPTools
from backend.app.orchestration.runner import MissionRunner
from backend.app.settings import AgentOverride
from backend.app.storage.api_connections import write_key


@contextmanager
def server(respond):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def handle_call(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            calls.append((self.path, dict(self.headers), json.loads(body) if body else None))
            try:
                respond(self, calls)
            except (BrokenPipeError, ConnectionResetError):
                pass
        do_POST = do_GET = handle_call
    class LocalServer(ThreadingHTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name = 'localhost'
            self.server_port = self.server_address[1]
    httpd = LocalServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield dict(base_url=f'http://127.0.0.1:{httpd.server_port}/v1', allow_private=True,
                   enabled=True, protocol='openai_chat', timeout=3), calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def sse(handler, frames):
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream')
    handler.end_headers()
    for frame in frames:
        handler.wfile.write(('data: ' + (frame if isinstance(frame, str) else json.dumps(frame)) + '\n\n').encode())
        handler.wfile.flush()


def frames(protocol, text='真实 API 文本'):
    if protocol == 'openai_responses':
        return [{'type': 'response.output_text.delta', 'delta': text}, {'type': 'response.completed'}]
    if protocol == 'anthropic':
        return [{'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': text}}, {'type': 'message_stop'}]
    return [{'choices': [{'delta': {'content': text}}]}, '[DONE]']


@pytest.mark.parametrize('protocol,path', [('openai_responses','/responses'), ('openai_chat','/chat/completions'), ('anthropic','/messages')])
def test_real_socket_protocol_and_auth(protocol, path, monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    with server(lambda h, c: sse(h, frames(protocol))) as (connection, calls):
        connection['protocol'] = protocol
        output = []
        stream_text(connection, 'secret-value', 'custom/unlisted', '你好', output.append)
        assert ''.join(output) == '真实 API 文本'
        assert calls[0][0] == '/v1' + path
        assert calls[0][2]['model'] == 'custom/unlisted'
        assert calls[0][2]['stream'] is True
        headers = calls[0][1]
        if protocol == 'anthropic':
            assert headers['x-api-key'] == 'secret-value'
            assert headers['anthropic-version'] == '2023-06-01'
        else:
            assert headers['Authorization'] == 'Bearer secret-value'
        assert 'tools' not in calls[0][2]


@pytest.mark.parametrize('status,code,retryable', [(401,'authentication',False), (400,'configuration',False), (302,'configuration',False), (500,'upstream',True)])
def test_errors_never_leak_body_or_follow_redirect(status, code, retryable):
    def respond(h, calls):
        h.send_response(status)
        h.send_header('Location', 'http://127.0.0.1:1/stolen')
        h.end_headers()
        h.wfile.write(b'secret-value private upstream diagnostic')
    with server(respond) as (connection, calls):
        with pytest.raises(APIError) as error:
            stream_text(connection, 'secret-value', 'model', 'hi', lambda t: None)
        assert error.value.code == code
        assert error.value.retryable is retryable
        assert 'secret' not in str(error.value)
        assert len(calls) == 1


def test_all_dns_results_are_validated_and_rebinding_cannot_reresolve(monkeypatch):
    calls = []
    def mixed(*args, **kwargs):
        calls.append(args)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8',443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1',443))]
    monkeypatch.setattr(socket, 'getaddrinfo', mixed)
    with pytest.raises(APIError, match='endpoint_policy'):
        with request(dict(base_url='https://public.test/v1', protocol='openai_chat'), 'key', 'GET', '/models'):
            pytest.fail('must reject mixed DNS answers')
    assert len(calls) == 1


def test_pinned_socket_never_resolves_hostname_twice(monkeypatch):
    with server(lambda h,c: sse(h, frames('openai_chat'))) as (connection, calls):
        port = int(connection['base_url'].split(':')[2].split('/')[0])
        connection['base_url'] = f'http://changing.test:{port}/v1'
        resolutions = []
        def resolve(*args, **kwargs):
            resolutions.append(args)
            assert len(resolutions) == 1
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
        monkeypatch.setattr(socket, 'getaddrinfo', resolve)
        stream_text(connection, 'key', 'model', 'hi', lambda t: None)
        assert len(resolutions) == 1
        assert calls[0][1]['Host'] == f'changing.test:{port}'


@pytest.mark.parametrize('send_headers', [False, True])
def test_cancel_interrupts_stalled_headers_or_stream(send_headers):
    ready = threading.Event()
    release = threading.Event()
    cancel = threading.Event()
    def respond(h, c):
        if send_headers:
            h.send_response(200)
            h.end_headers()
        ready.set()
        release.wait(4)
    with server(respond) as (connection, calls):
        errors = []
        def run():
            try:
                stream_text(connection, 'key', 'model', 'hi', lambda t: None, cancel.is_set)
            except APIError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=run)
        thread.start()
        assert ready.wait(2)
        cancel.set()
        thread.join(1)
        release.set()
        assert not thread.is_alive()
        assert errors == ['stopped']


def test_catalog_get_stale_version_isolation_and_unknown_vendor(temp_settings):
    failure = []
    def respond(h,c):
        h.send_response(500 if failure else 200)
        h.end_headers()
        h.wfile.write(json.dumps({'data': [{'id':'unknown/custom', 'owned_by':'new-vendor'}]}).encode())
    with server(respond) as (connection,calls):
        connection['key_ref'] = write_key(temp_settings, 'catalog-key')
        initial = catalog(temp_settings, 'api-test', connection)
        assert initial['status'] == 'error' and initial['fetched_at'] is None
        assert calls == []
        result = catalog(temp_settings, 'api-test', connection, refresh=True)
        assert result['status'] == 'ready'
        assert result['models'] == [{'id':'unknown/custom', 'label':'unknown/custom', 'vendor':'new-vendor'}]
        failure.append(True)
        stale = catalog(temp_settings, 'api-test', connection, refresh=True)
        assert stale['status'] == 'stale' and stale['models'] == result['models']
        assert stale['fetched_at'] == result['fetched_at']
        connection['key_ref'] = write_key(temp_settings, 'rotated-key')
        assert catalog(temp_settings, 'api-test', connection)['models'] == []


def test_api_adapter_publishes_text_and_redacts_secret(repository, temp_settings):
    mission = Mission(id='api-mission', title='chat', goal='explain', status='running')
    repository.create_mission(mission)
    tools = MCPTools(repository, temp_settings.artifact_root)
    with server(lambda h,c: sse(h, frames('openai_chat', 'hello secret-value'))) as (connection,calls):
        connection['key_ref'] = write_key(temp_settings, 'secret-value')
        outcome = APITextAdapter(temp_settings, connection, 'model').run_turn(mission.id, 'chulan','turn-api','api-test','hi',tools)
    assert outcome.status == TurnStatus.COMPLETED
    assert repository.list_messages(mission.id)[0].content == 'hello [redacted]'
    assert 'secret-value' not in json.dumps([e.payload for e in repository.list_events_after(mission.id,0)])


@pytest.mark.parametrize('partial', [False, True])
def test_api_partial_output_prevents_retry(repository, temp_settings, partial):
    mission = Mission(id='api-partial',title='chat',goal='hi',status='running')
    repository.create_mission(mission)
    def respond(h,c):
        sse(h, ([{'choices':[{'delta':{'content':'partial'}}]}] if partial else []) + [{'error':{'message':'secret-value'}}])
    with server(respond) as (connection,calls):
        connection['key_ref'] = write_key(temp_settings,'secret-value')
        result = APITextAdapter(temp_settings,connection,'model').run_turn(mission.id,'chulan','turn-p','api-test','hi',MCPTools(repository,temp_settings.artifact_root))
    assert result.retryable is (not partial)
    assert result.output_started is partial
    assert repository.list_messages(mission.id) == []


@pytest.mark.parametrize('approved,first_code,first_partial,expected', [(True,'upstream',False,2),(False,'upstream',False,1),(True,'authentication',False,1),(True,'upstream',True,1)])
def test_mixed_cli_api_explicit_fallback_guard(repository, temp_settings, approved, first_code, first_partial, expected):
    connection = dict(base_url='https://example.com/v1',protocol='openai_chat',allow_private=False)
    temp_settings.api_connections = {'api-test':connection}
    temp_settings.agent_overrides = {'chulan':AgentOverride(fallback_context_approved=approved,
        fallback_context_targets={'api-test':endpoint_binding(connection)})}
    class Adapter:
        def __init__(self):
            self.calls=[]
        def run_turn(self,*args):
            self.calls.append(args[3])
            if len(self.calls)==1:
                return TurnOutcome(status=TurnStatus.BLOCKED, failure_code=first_code,
                    retryable=first_code!='authentication', output_started=first_partial)
            return TurnOutcome(status=TurnStatus.COMPLETED)
    mission=Mission(id='mixed',title='mixed',goal='hi',status='running')
    repository.create_mission(mission)
    adapter=Adapter()
    runner=MissionRunner(repository,temp_settings,adapter)
    runner._run_turn_with_fallbacks(mission,'chulan','mixed-turn',['codex','api-test'],'hi',MCPTools(repository,temp_settings.artifact_root))
    assert len(adapter.calls)==expected
    connection['base_url']='https://other.example/v1'
    assert not context_approved(temp_settings,'chulan','api-test')


@pytest.mark.parametrize('protocol', ['openai_responses','openai_chat','anthropic'])
def test_router_real_api_discussion_submission(repository, temp_settings, protocol):
    from backend.app.api.mission_requests import CreateMissionRequest, create_requested_mission
    from backend.app.api.missions import _build_mission_adapter
    from backend.app.extensions.discussion.deliberation import Deliberation
    from backend.tests.test_deliberation import opinion
    expected = opinion('API 真实协议的结构化提交')
    with server(lambda h,c: sse(h, frames(protocol,json.dumps(expected, ensure_ascii=False)))) as (connection,calls):
        connection.update(protocol=protocol, key_ref=write_key(temp_settings,'discussion-secret'))
        temp_settings.api_connections={'api-test':connection}
        temp_settings.use_fake_cli=False
        temp_settings.agent_overrides={aid:AgentOverride(default_provider_id='api-test',fallback_provider_ids=[],provider_models={'api-test':'unlisted-model'}) for aid in ['chulan','baobao']}
        mission=create_requested_mission(repository,temp_settings,CreateMissionRequest(goal='讨论方案',room_mode='roundtable',discussion={'protocol':'deliberation_v2','participant_ids':['chulan','baobao'],'max_exchange_rounds':2}))[0]
        assert mission.discussion.provider_routes['chulan']==['api-test']
        policy=Deliberation(repository)
        reserved=policy.claim_batch(mission.id,temp_settings.artifact_root)[0]
        lease=policy.authorize(mission.id,reserved['turn_id'],'api-test')
        tools=MCPTools(repository,temp_settings.artifact_root,lease_token=lease['token'])
        adapter=_build_mission_adapter(temp_settings)
        from backend.app.extensions.discussion.hooks import run_deliberation_turn
        result=run_deliberation_turn(adapter,mission.id,lease['agent_id'],lease['turn_id'],'api-test','角色定义【本轮协议】禁止工具',tools)
        assert result.status==TurnStatus.COMPLETED, result
        settled=policy.state(repository.get_mission(mission.id))['leases'][lease['turn_id']]
        assert settled['status']=='submitted'
        assert policy.get(mission.id,settled['result_id'])['data']['position_summary']==expected['position_summary']
        assert not [m for m in repository.list_messages(mission.id) if m.role == 'agent'] # sealed JSON is not a public message
        assert len(calls)==1
        assert 'discussion-secret' not in json.dumps(temp_settings.model_dump(mode='json'))


def test_catalog_http_routes_are_real_and_do_not_probe_on_get(tmp_path):
    from backend.tests.test_hub_settings_api import _make_client
    client,settings=_make_client(tmp_path)
    with server(lambda h,c:(h.send_response(200),h.end_headers(),h.wfile.write(b'{"data":[{"id":"custom-only"}]}'))) as (connection,calls):
        response=client.put('/api/hub/settings',json={'connections':{'api-live':dict(connection,key_action='replace',api_key='catalog-secret')}})
        assert response.status_code==200,response.text
        assert client.get('/api/hub/connections/api-live/models').json()['status']=='error'
        assert calls==[]
        result=client.post('/api/hub/connections/api-live/models/refresh').json()
        assert result['status']=='ready'
        assert result['models'][0]['id']=='custom-only'
        assert client.get('/api/hub/connections/api-live/models').json()==result
        assert len(calls)==1
        assert client.get('/api/hub/connections/missing/models').status_code==404
        claude=client.get('/api/hub/connections/claude/models').json()
        assert claude['status']=='ready' and [m['id'] for m in claude['models']]==['fable','opus','sonnet']
        assert client.get('/api/hub/connections/fake/models').json()['status']=='unsupported'


def test_key_rotation_keeps_inflight_request_but_retired_snapshot_fails_closed(repository,temp_settings):
    from backend.app.storage.api_connections import retire_keys
    started=threading.Event()
    release=threading.Event()
    def respond(h,c):
        started.set()
        release.wait(3)
        sse(h,frames('openai_chat'))
    mission=Mission(id='rotation',title='chat',goal='hi',status='running')
    repository.create_mission(mission)
    with server(respond) as (connection,calls):
        connection['key_ref']=write_key(temp_settings,'original-key')
        adapter=APITextAdapter(temp_settings,connection,'model')
        results=[]
        worker=threading.Thread(target=lambda:results.append(adapter.run_turn(mission.id,'chulan','rotation-1','api-test','hi',MCPTools(repository,temp_settings.artifact_root))))
        worker.start()
        assert started.wait(2)
        new=dict(connection,key_ref=write_key(temp_settings,'rotated-key'))
        retire_keys(temp_settings,{'api-test':connection},{'api-test':new})
        release.set()
        worker.join(2)
        assert results[0].status==TurnStatus.COMPLETED
        second=adapter.run_turn(mission.id,'chulan','rotation-2','api-test','hi',MCPTools(repository,temp_settings.artifact_root))
        assert second.failure_code=='configuration' and second.retryable is False
        assert len(calls)==1
        assert calls[0][1]['Authorization']=='Bearer original-key'


def test_dns_resolution_cancellation_never_sends_request(monkeypatch):
    ready=threading.Event()
    release=threading.Event()
    cancel=threading.Event()
    def resolve(*args,**kwargs):
        ready.set()
        release.wait(2)
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',1))]
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    errors=[]
    def run():
        try:
            with request(dict(base_url='https://changing.test',protocol='openai_chat'), 'secret','GET','/models',cancelled=cancel.is_set):
                pytest.fail('cancelled resolver must never connect')
        except APIError as exc:
            errors.append(exc.code)
    worker=threading.Thread(target=run)
    worker.start()
    assert ready.wait(1)
    cancel.set()
    worker.join(1)
    release.set()
    assert not worker.is_alive()
    assert errors==['stopped']


def test_cli_tool_activity_prevents_silent_replay(repository,temp_settings):
    mission=Mission(id='cli-tools',title='chat',goal='hi',status='running')
    repository.create_mission(mission)
    class Adapter:
        calls=[]
        def run_turn(self,*args):
            self.calls.append(args[3])
            repository.add_event(mission.id,'provider.activity',{'tool_name':'terminal','phase':'started'})
            return TurnOutcome(status=TurnStatus.BLOCKED)
    adapter=Adapter()
    MissionRunner(repository,temp_settings,adapter)._run_turn_with_fallbacks(mission,'chulan','tool-turn',['codex','claude'],'hi',MCPTools(repository,temp_settings.artifact_root))
    assert adapter.calls==['codex']


def test_paginated_catalog_and_failed_next_page_retains_old_directory(temp_settings):
    fail=[]
    def respond(h,c):
        if len(c)%2:
            result={'data':[{'id':'custom/first'}],'has_more':True,'last_id':'custom/first'}
        else:
            result={'data':[{'id':'custom/second','display_name':'Second model'}],'has_more':False}
        h.send_response(500 if fail and len(c)%2==0 else 200)
        h.end_headers()
        h.wfile.write(json.dumps(result).encode())
    with server(respond) as (connection,calls):
        connection.update(protocol='anthropic',key_ref=write_key(temp_settings,'pagination-secret'))
        result=catalog(temp_settings,'api-paged',connection,refresh=True)
        assert result['status']=='ready'
        assert [m['id'] for m in result['models']]==['custom/first','custom/second']
        assert calls[1][0]=='/v1/models?after_id=custom%2Ffirst'
        fail.append(True)
        stale=catalog(temp_settings,'api-paged',connection,refresh=True)
        assert stale['status']=='stale'
        assert stale['models']==result['models']


def test_saved_approval_is_bound_to_endpoint_and_snapshot_excludes_connections(tmp_path):
    from backend.tests.test_hub_settings_api import _make_client
    from backend.app.settings import snapshot_settings
    from backend.app.storage.hub_settings import default_path,load
    client,settings=_make_client(tmp_path)
    response=client.put('/api/hub/settings',json={'connections':{'api-test':dict(name='Test',base_url='https://example.com/v1',protocol='openai_chat',key_action='replace',api_key='binding-secret')},'agents':{'chulan':{'fallback_provider_ids':['api-test'],'provider_models':{'api-test':'model'},'fallback_context_approved':True}}})
    assert response.status_code==200,response.text
    snapshot=snapshot_settings(settings)
    assert context_approved(snapshot,'chulan','api-test')
    assert 'api_connections' not in snapshot.model_dump()
    assert 'key_ref' not in snapshot.model_dump_json()
    response=client.put('/api/hub/settings',json={'connections':{'api-test':{'base_url':'https://changed.example/v1','key_action':'replace','api_key':'changed-secret'}}})
    assert response.status_code==200,response.text
    updated=snapshot_settings(settings)
    assert not context_approved(updated,'chulan','api-test')
    # A mission snapshot keeps its original destination; a settings mutation
    # never silently changes an already authorized outgoing context destination.
    assert context_approved(snapshot,'chulan','api-test')
    assert load(default_path(settings))['agents']['chulan']['fallback_context_approved'] is False


@pytest.mark.parametrize('approved', [False, True])
def test_cli_failure_to_real_api_requires_context_consent(repository,temp_settings,monkeypatch,approved):
    from backend.app.api.missions import _build_mission_adapter
    from backend.app.adapters.mcp_subprocess import MCPSubprocessAdapter
    from backend.app.settings import ProviderOverride
    attempted=[]
    def cli_failure(self,*args):
        attempted.append(args[3])
        return TurnOutcome(status=TurnStatus.BLOCKED,summary='transient unavailable',retryable=True)
    monkeypatch.setattr(MCPSubprocessAdapter,'run_turn',cli_failure)
    with server(lambda h,c:sse(h,frames('openai_chat'))) as (connection,calls):
        connection['key_ref']=write_key(temp_settings,'fallback-secret')
        temp_settings.use_fake_cli=False
        temp_settings.api_connections={'api-test':connection}
        temp_settings.provider_overrides={'codex':ProviderOverride(enabled=True)}
        temp_settings.agent_overrides={'chulan':AgentOverride(default_provider_id='codex',fallback_provider_ids=['api-test'],provider_models={'api-test':'model'},fallback_context_approved=approved,fallback_context_targets={'api-test':endpoint_binding(connection)})}
        mission=Mission(id='real-fallback',title='chat',goal='hi',status='running')
        repository.create_mission(mission)
        router=_build_mission_adapter(temp_settings)
        result=MissionRunner(repository,temp_settings,router)._run_turn_with_fallbacks(mission,'chulan','fallback-turn',['codex','api-test'],'sensitive context',MCPTools(repository,temp_settings.artifact_root))
        assert attempted==['codex']
        assert len(calls)==int(approved)
        assert (result.status==TurnStatus.COMPLETED) is approved
        if approved:
            assert repository.list_messages(mission.id)[0].provider_id=='api-test'
            assert 'sensitive context' in calls[0][2]['messages'][0]['content']
        else:
            assert repository.list_messages(mission.id)==[]


def test_shared_budget_interrupts_real_socket_before_connection_timeout():
    from backend.app.adapters.turn_budget import budgeted_turn
    release = threading.Event()

    def respond(handler, calls):
        handler.send_response(200)
        handler.end_headers()
        release.wait(4)

    with server(respond) as (connection, calls):
        finished = threading.Event()
        errors = []

        @budgeted_turn(.15)
        def run():
            try:
                stream_text(connection, 'key', 'model', 'hi', lambda text: None)
            except APIError as exc:
                errors.append(exc.code)
            finally:
                finished.set()

        thread = threading.Thread(target=run)
        thread.start()
        try:
            assert finished.wait(1), 'shared deadline did not interrupt the socket'
            assert errors == ['timeout']
            assert len(calls) == 1
        finally:
            release.set()
            thread.join(2)
