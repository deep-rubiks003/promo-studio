import sys,time,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from test_core import SCRIPT

def test_discovery_is_additive_and_preview_is_local(tmp_path,monkeypatch):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'discovery.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    monkeypatch.setitem(server.state,'key','')
    p=server.create(dict(name='Local test',files=[dict(name='one.txt',text=SCRIPT)]))
    p.update(groups=[dict(id='manual',title='Keep this draft',variations=[])],brief={'manual':'unchanged'},proposals={'keep':'proposal'})
    server.save(p);original=copy.deepcopy(p)
    client=TestClient(server.app);headers={'X-Studio-Token':server.TOKEN}
    body=dict(selected_ids=[p['episodes'][0]['id']],structure='either',quantity='cap',limit=3,minimum=20,maximum=60)
    monkeypatch.setattr(server,'call',lambda *args:(_ for _ in ()).throw(AssertionError('No AI in preview')))
    r=client.post('/api/projects/'+p['id']+'/discovery/preview',json=body,headers=headers)
    assert r.status_code==200,r.text
    assert r.json()['batch_count']>=1 and 'cost_upper_usd' in r.json()
    assert 'windows' not in r.json()
    assert server.load(p['id'])==original
    # Explicit start is gated by the private connection, not upload/preview.
    assert client.post('/api/projects/'+p['id']+'/discovery',json=body,headers=headers).status_code==400
    assert 'discovery_runs' not in server.load(p['id'])
    monkeypatch.setitem(server.state,'key','mock-only')
    def fake_run(project,run,call,save,material,**kwargs):
        assert kwargs['review_enabled'] is False
        run.update(status='done',shortlist=[],coverage=[],rejections=[])
    monkeypatch.setattr(server,'run_discovery',fake_run)
    result=client.post('/api/projects/'+p['id']+'/discovery',json=body,headers=headers)
    assert result.status_code==200,result.text
    rid=result.json()['run_id'];jid=result.json()['job_id']
    for _ in range(100):
        j=client.get('/api/jobs/'+jid).json()
        if j['status'] in ('done','error'):break
        time.sleep(.01)
    assert j['status']=='done' and j['run_id']==rid
    updated=server.load(p['id'])
    for key in ('groups','brief','proposals','episodes'):assert updated[key]==original[key]
    view=client.get('/api/projects/'+p['id']+'/discovery/'+rid+'/view').json()
    assert view['discovery_view']==rid and view['groups']==[]
    assert server.load(p['id'])['groups']==original['groups']
    report=client.get('/api/projects/'+p['id']+'/discovery/'+rid+'/export').json()
    assert report['discovery']['id']==rid and len(report['episodes'])==1
