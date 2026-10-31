from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.domain.models import Conversation, ConversationStatus, Mission, MissionKind


def test_index_scope_identity_pagination_search(repository, temp_settings):
    for cid, kind, status in [('c1','chat','active'), ('c2','chat','active'),('ct','task','active'),('ca','chat','archived')]:
        repository.create_conversation(Conversation(id=cid,title=cid,kind=kind,status=ConversationStatus(status)))
    for mid,cid in [('m1','c1'),('m2','c2'),('mt','ct'),('ma','ca'),('orphan',None)]:
        repository.create_mission(Mission(id=mid,title='test',goal='test',conversation_id=cid))
        repository.add_event(mid,'artifact',{'path':'demo.html','title':'old'})
    repository.create_mission(Mission(id='verification',title='verify',goal='verify',conversation_id='c1',kind=MissionKind.PROVIDER_VERIFICATION))
    repository.add_event('verification','artifact',{'path':'hidden.md'})
    repository.add_event('m1','artifact',{'path':'demo.html','title':'latest'})
    repository.add_event('m1','artifact',{'path':'/private/secret.txt'})
    repository.add_event('m1','artifact',{'path':'../escape.txt'})
    repository.add_event('m1','artifact',{'path':'C:/secret.txt'})
    client=TestClient(create_app(settings=temp_settings))
    response=client.get('/api/artifacts').json()
    assert [(a['mission_id'],a['title']) for a in response['items']] == [('m1','latest'),('m2','old')]
    assert all(a['conversation_title'] in {'c1','c2'} for a in response['items'])
    assert 'artifact_root' not in str(response) and '/private' not in str(response)
    assert len(client.get('/api/artifacts?include_archived=true').json()['items']) == 3
    assert len(client.get('/api/artifacts?q=LATEST').json()['items']) == 1
    assert len(client.get('/api/artifacts?q=demo.HTML').json()['items']) == 2
    first=client.get('/api/artifacts?limit=1').json()
    second=client.get('/api/artifacts',params={'limit':1,'cursor':first['next_cursor']}).json()
    assert first['items'][0]['id'] != second['items'][0]['id']
    assert client.get('/api/artifacts',params={'cursor':second['next_cursor']}).json()['items']==[]
    assert client.get('/api/artifacts?cursor=nope').status_code==422

    assert client.get('/api/artifacts?cursor=99999999999999999999999999').status_code == 422
