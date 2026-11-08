import importlib
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from test_core import SCRIPT

def test_local_security_persistence_and_atomic_failure(tmp_path,monkeypatch):
    monkeypatch.setenv('PROMO_STUDIO_DATA',str(tmp_path))
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'test.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    client=TestClient(server.app);token=client.get('/api/status').json()['token'];headers={'X-Studio-Token':token}
    assert client.post('/api/projects',json={}).status_code==403
    assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==403
    assert client.post('/api/projects',json={},headers={**headers,'Origin':'https://evil.example'}).status_code==403
    p=client.post('/api/projects',json={'name':'Test','files':[{'name':'one.txt','text':SCRIPT}]},headers=headers).json()
    assert p['episodes'][0]['events'][0]['text']=='"Who took my letter?"'
    assert client.get('/api/projects/'+p['id']).json()['name']=='Test'
    monkeypatch.setattr(server.Gemini,'validate',lambda self:None)
    connected=client.post('/api/connect',json={'key':'private-test-key','model':'gemini-3.8-flash','budget':2},headers=headers)
    assert connected.status_code==200 and 'private-test-key' not in connected.text
    def failed(*args):raise ValueError('Simulated provider failure')
    monkeypatch.setattr(server,'call',failed)
    jid=client.post('/api/projects/'+p['id']+'/analyze',json={'selected_ids':[p['episodes'][0]['id']]},headers=headers).json()['job_id']
    for _ in range(100):
        job=client.get('/api/jobs/'+jid).json()
        if job['status'] in ('done','error'):break
        time.sleep(.01)
    assert job['status']=='error'
    assert client.get('/api/projects/'+p['id']).json()['analysis'] is None
    output=client.get('/api/projects/'+p['id']+'/export')
    assert 'private-test-key' not in output.text and output.json()['ranked_variations']==[]

def test_successful_generation_and_invalid_proposal(tmp_path,monkeypatch):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'success.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    client=TestClient(server.app);headers={'X-Studio-Token':server.TOKEN}
    p=server.create({'name':'Test','files':[{'name':'one.txt','text':SCRIPT}]});ep=p['episodes'][0]
    p['analysis']={'title':'Test'};p['analysis_ids']=[ep['id']];server.save(p);server.state.update(key='fake',keyhash='fake')
    v={'angle':'Mystery','distinction':'Opening and ending differ','segments':[{'episode_id':ep['id'],'start':0,'end':25}],'beats':[{'role':role,'event_ids':[ep['events'][idx]['id']],'reason':'Source'} for role,idx in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],'question':'What will she do?','weakness':'Timing','reason':'Conflict'}
    def fake(pid,stage,prompt):
        if stage=='candidate-generation':return {'groups':[{'title':'Letter','opportunity':'Missing letter','variations':[v.copy()]}],'failures':['Only one supported angle.']}
        return {'narrative':dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),'review_notes':'Works','rubric':[{'criterion':k,'score':4,'reason':'Good'} for k in server.validate_cut.__globals__['WEIGHTS']]}
    monkeypatch.setattr(server,'call',fake)
    result=client.post('/api/projects/'+p['id']+'/generate',headers=headers,json={'selected_ids':[ep['id']],'minimum':20,'maximum':60,'structure':'continuous','group_count':1})
    jid=result.json()['job_id']
    for _ in range(100):
        job=client.get('/api/jobs/'+jid).json()
        if job['status'] in ('done','error'):break
        time.sleep(.01)
    assert job['status']=='done',job
    export=client.get('/api/projects/'+p['id']+'/export').json()
    assert not export['ranked_variations'] and export['unranked_variations'][0]['validation']['score'] is None
    assert 'posting_priority' not in export['unranked_variations'][0]
    vid=export['unranked_variations'][0]['id']
    jid=client.post('/api/projects/'+p['id']+'/virality-review',headers=headers,json={'variation_id':vid}).json()['job_id']
    for _ in range(100):
        job=client.get('/api/jobs/'+jid).json()
        if job['status'] in ('done','error'):break
        time.sleep(.01)
    assert job['status']=='done',job
    export=client.get('/api/projects/'+p['id']+'/export').json()
    assert export['ranked_variations'][0]['validation']['score']==80
    assert export['ranked_variations'][0]['posting_priority']==1

def test_analysis_requires_selection_and_never_sends_other_episodes(tmp_path,monkeypatch):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'selection.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    client=TestClient(server.app);headers={'X-Studio-Token':server.TOKEN}
    p=server.create({'name':'Selection','files':[{'name':'one.txt','text':SCRIPT},{'name':'two.txt','text':SCRIPT.replace('letter','SECRET_UNSELECTED')}]})
    captured=[]
    def fake(pid,stage,prompt):
        captured.append(prompt)
        return {'title':'Selected story','logline':'Conflict','primary_genre':'Drama','secondary_genre':'Mystery'}
    monkeypatch.setattr(server,'call',fake);server.state.update(key='fake',keyhash='fake')
    assert client.post('/api/projects/'+p['id']+'/analyze',json={},headers=headers).status_code==400
    assert not captured
    jid=client.post('/api/projects/'+p['id']+'/analyze',json={'selected_ids':[p['episodes'][0]['id']]},headers=headers).json()['job_id']
    for _ in range(100):
        job=client.get('/api/jobs/'+jid).json()
        if job['status'] in ('done','error'):break
        time.sleep(.01)
    assert job['status']=='done'
    assert 'SECRET_UNSELECTED' not in captured[0]
    assert server.load(p['id'])['analysis_ids']==[p['episodes'][0]['id']]

def test_resume_reuses_generation_and_completed_review(tmp_path,monkeypatch):
    import server
    from parser import WEIGHTS
    monkeypatch.setattr(server,'DB',tmp_path/'resume.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    p=server.create({'name':'Resume','files':[{'name':'one.txt','text':SCRIPT}]});ep=p['episodes'][0]
    brief=dict(selected_ids=[ep['id']],minimum=20,maximum=60,structure='continuous',group_count=1)
    p.update(analysis={'title':'Test'},analysis_ids=[ep['id']]);server.save(p)
    def variant(start,indices):return dict(angle='Angle',distinction='Different source opening',segments=[dict(episode_id=ep['id'],start=start,end=25)],beats=[dict(role=role,event_ids=[ep['events'][idx]['id']],reason='Evidence') for role,idx in zip(['Hook','Context','Escalation','Cliffhanger'],indices)],question='What happens next?')
    variants=[variant(0,[0,1,1,2]),variant(5,[1,1,2,2])]
    response={'groups':[dict(title='Letter',opportunity='Conflict',variations=variants)]}
    review=dict(narrative=dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),rubric=[dict(criterion=k,score=4,reason='Clear') for k in WEIGHTS],review_notes='Good')
    def raw(result):return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(result)}]}}]}
    with server.db() as c:
        c.execute('INSERT INTO raw VALUES (?,?,?,?)',('gen',p['id'],'candidate-generation',json.dumps(dict(received_at=1,brief=brief,response=raw(response)))))
        c.execute('INSERT INTO raw VALUES (?,?,?,?)',('review',p['id'],'independent-clip-review',json.dumps(dict(received_at=2,response=raw(review)))))
    calls=[]
    def fake(pid,stage,prompt):calls.append(stage);return review
    monkeypatch.setattr(server,'call',fake);server.state.update(key='fake',keyhash='fake')
    client=TestClient(server.app)
    jid=client.post('/api/projects/'+p['id']+'/resume',headers={'X-Studio-Token':server.TOKEN},json={}).json()['job_id']
    for _ in range(100):
        job=client.get('/api/jobs/'+jid).json()
        if job['status'] in ('done','error'):break
        time.sleep(.01)
    assert job['status']=='done',job
    assert calls==[]
    variations=server.load(p['id'])['groups'][0]['variations']
    assert len(variations)==2
    assert variations[0]['validation']['score']==80 and variations[1]['validation']['score'] is None
