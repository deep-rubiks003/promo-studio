import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from test_core import SCRIPT
from prompt_payload import unpack_events
import server

@pytest.fixture
def studio(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB',tmp_path/'studio.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    monkeypatch.setitem(server.state,'key','test-key')
    p=server.create({'name':'Show','files':[{'name':'one.txt','text':SCRIPT},{'name':'two.txt','text':SCRIPT.replace('Episode 1','Episode 2').replace('letter','necklace')},{'name':'three.txt','text':SCRIPT.replace('Episode 1','Episode 3').replace('letter','UNSELECTED_SECRET')}]})
    client=TestClient(server.app)
    return p,client,{'X-Studio-Token':server.TOKEN}

def finish(client,jid):
    for _ in range(200):
        j=client.get('/api/jobs/'+jid).json()
        if j['status'] in ('done','error'):return j
        time.sleep(.01)
    raise AssertionError('Job did not finish')

def test_one_or_multiple_grouping(studio):
    p,_,_=studio;ids=[e['id'] for e in p['episodes'][:2]]
    assert server.build_brief(p,dict(selected_ids=ids,promo_mode='single'))['episode_groups']==[ids]
    assert server.build_brief(p,dict(selected_ids=ids,promo_mode='multiple'))['episode_groups']==[[ids[0]],[ids[1]]]
    assert server.build_brief(p,dict(selected_ids=ids,promo_mode='multiple',episode_groups=[ids]))['episode_groups']==[ids]
    with pytest.raises(ValueError):server.build_brief(p,dict(selected_ids=ids,promo_mode='multiple',episode_groups=[[ids[0]],[ids[0]]]))
    with pytest.raises(ValueError):server.build_brief(p,dict(selected_ids=ids,promo_mode='single',episode_groups=[[p['episodes'][2]['id']]]))

def test_generation_needs_no_analysis_and_one_draft_per_group(studio,monkeypatch):
    p,c,h=studio;ids=[e['id'] for e in p['episodes'][:2]];calls=[]
    def fake(pid,stage,prompt):
        calls.append((stage,prompt))
        assert 'UNSELECTED_SECRET' not in prompt
        ep=next(e for e in p['episodes'] if e['id'] in prompt)
        if stage=='candidate-generation':
            v=dict(angle='Letter conflict',distinction='One focused source draft',segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[dict(role=role,event_ids=[ep['events'][i]['id']],reason='Evidence') for role,i in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],question='What happens next?',weakness='Timing',reason='Conflict')
            return dict(title='A conflict',opportunity='The missing letter',promo=v)
        return dict(narrative=dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),rubric=[dict(criterion=k,score=4,reason='Clear') for k in server.validate_cut.__globals__['WEIGHTS']],review_notes='Clear')
    monkeypatch.setattr(server,'call',fake)
    r=c.post('/api/projects/'+p['id']+'/generate',headers=h,json=dict(selected_ids=ids,promo_mode='multiple',minimum=20,maximum=60,structure='continuous'))
    assert r.status_code==200,r.text
    assert finish(c,r.json()['job_id'])['status']=='done'
    saved=server.load(p['id']);assert saved['analysis'] is None
    assert len(saved['groups'])==2 and all(len(g['variations'])==1 for g in saved['groups'])
    assert all(g['variations'][0]['validation']['eligible'] for g in saved['groups'])
    assert [g['source_ids'] for g in saved['groups']]==[[ids[0]],[ids[1]]]
    assert len([s for s,_ in calls if s=='candidate-generation'])==2
    # Optional spoiler check: no call was made automatically.
    assert not any(s=='spoiler-check' for s,_ in calls)

def test_spoiler_checker_scope_and_stale_reset(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0];vid='draft-one'
    v=dict(id=vid,angle='Draft',segments=[dict(episode_id=ep['id'],start=0,end=25)],question='Who has it?',beats=[])
    p.update(brief=dict(selected_ids=[ep['id']],minimum=20,maximum=60,structure='continuous'),groups=[dict(id='g',title='Draft',source_ids=[ep['id']],variations=[v])]);server.save(p)
    captured=[]
    def fake(pid,stage,prompt):
        captured.append((stage,prompt));assert 'UNSELECTED_SECRET' not in prompt
        return dict(results=[dict(promo_id=vid,status='warning',summary='The answer is revealed.',findings=[dict(severity='high',kind='Resolved question',event_ids=[ep['events'][1]['id']],explanation='It answers who has the letter.',suggestion='End before this reveal.')])])
    monkeypatch.setattr(server,'call',fake)
    r=c.post('/api/projects/'+p['id']+'/spoiler-check',headers=h,json={'variation_id':vid})
    assert r.status_code==200,r.text
    assert finish(c,r.json()['job_id'])['status']=='done'
    check=server.load(p['id'])['groups'][0]['variations'][0]['spoiler_check']
    assert check['status']=='warning' and check['source_ids']==[ep['id']]
    assert check['findings'][0]['events'][0]['text']==ep['events'][1]['text']
    assert check['findings'][0]['events'][0]['episode_number']==ep['number']
    assert captured[0][0]=='spoiler-check'

def test_import_metadata_is_local_and_exact(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB',tmp_path/'metadata.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    monkeypatch.setitem(server.state,'key','')
    def no_ai(*args):raise AssertionError('Import must never call AI')
    monkeypatch.setattr(server,'call',no_ai)
    line='A disputed letter changes everything — will Alice believe Bob?'
    p=server.create(dict(name='No AI import',files=[dict(name='script.txt',text=SCRIPT.replace('EPISODE NUMBER: Episode 1','EPISODE NUMBER: Episode 17\nEPISODE LOGLINE: '+line))]))
    assert p['episodes'][0]['number']==17
    assert p['episodes'][0]['logline']==line
    assert p['analysis'] is None and p['groups']==[]

def test_spoiler_checker_cannot_clear_unverifiable_evidence(studio,monkeypatch):
    from workflow import check_spoilers
    p,_,_=studio;ep=p['episodes'][0]
    v=dict(segments=[dict(episode_id=ep['id'],start=0,end=25)],question='Who has it?')
    def fake(*args):return dict(status='clear',summary='A claim with invalid evidence',findings=[dict(event_ids=['invented:0001'],kind='Reveal')])
    result=check_spoilers(p,v,[ep['id']],fake,server.material)
    assert result['status']=='inconclusive' and result['findings']==[]

def test_multiple_promos_resume_without_regenerating_completed_groups(studio,monkeypatch):
    p,c,h=studio;ids=[e['id'] for e in p['episodes'][:2]];counts={'drafts':0,'reviews':0}
    def fake(pid,stage,prompt):
        ep=next(e for e in p['episodes'] if e['id'] in prompt)
        if stage=='candidate-generation':
            counts['drafts']+=1
            if counts['drafts']==2:raise ValueError('Temporary provider failure')
            return dict(title='Draft',promo=dict(angle='Conflict',distinction='One draft',segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[dict(role=role,event_ids=[ep['events'][i]['id']],reason='Source') for role,i in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],question='What happens next?'))
        counts['reviews']+=1
        if counts['reviews']==2:raise ValueError('Temporary provider failure')
        return dict(narrative=dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),rubric=[dict(criterion=k,score=4,reason='Clear') for k in server.validate_cut.__globals__['WEIGHTS']],review_notes='Clear')
    monkeypatch.setattr(server,'call',fake)
    r=c.post('/api/projects/'+p['id']+'/generate',headers=h,json=dict(selected_ids=ids,promo_mode='multiple'))
    assert finish(c,r.json()['job_id'])['status']=='error'
    assert server.load(p['id'])['groups']==[]
    assert len(server.load(p['id'])['pending_generation']['groups'])==1
    r=c.post('/api/projects/'+p['id']+'/resume',headers=h,json={})
    assert finish(c,r.json()['job_id'])['status']=='done'
    assert counts=={'drafts':3,'reviews':0}
    assert len(server.load(p['id'])['groups'])==2
    assert 'pending_generation' not in server.load(p['id'])

def test_applied_recut_invalidates_previous_spoiler_check(studio):
    import copy
    p,c,h=studio;ep=p['episodes'][0];vid='draft'
    v=dict(id=vid,segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[dict(role=role,event_ids=[ep['events'][i]['id']],reason='Source') for role,i in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],question='What next?',narrative=dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),rubric=[dict(criterion=k,score=4,reason='Clear') for k in server.validate_cut.__globals__['WEIGHTS']],spoiler_check=dict(status='clear'))
    p.update(brief=dict(selected_ids=[ep['id']],minimum=20,maximum=60,structure='continuous'),groups=[dict(id='g',title='Draft',source_ids=[ep['id']],variations=[v])],proposals={vid:dict(variation=copy.deepcopy(v),change='Recut',tradeoff='Changed')})
    server.save(p)
    r=c.post('/api/projects/'+p['id']+'/apply',headers=h,json={'variation_id':vid})
    assert r.status_code==200,r.text
    assert 'spoiler_check' not in r.json()['groups'][0]['variations'][0]

def test_spoiler_checker_uses_stitched_playback_order(studio):
    from workflow import check_spoilers
    p,_,_=studio;a,b=p['episodes'][:2];captured=[]
    v=dict(segments=[dict(episode_id=b['id'],start=0,end=25),dict(episode_id=a['id'],start=0,end=25)],beats=[],question='What follows?')
    def fake(pid,stage,prompt):
        captured.append(prompt)
        return dict(status='clear',summary='No major reveal detected.',findings=[])
    check_spoilers(p,v,[a['id'],b['id']],fake,server.material)
    events=unpack_events(json.loads(captured[0].split(' PROMO EVENTS:')[1].split(' SELECTED EPISODE CONTEXT:')[0]))
    assert events[0]['episode_id']==b['id'] and events[0]['playback_start']==0
    assert next(e for e in events if e['episode_id']==a['id'])['playback_start']==25
