import copy
from test_new_workflow import studio
from test_core import SCRIPT
import server

def test_saved_output_is_available_without_key_or_model_calls(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0]
    v=dict(id='pending',angle='Saved draft',segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[dict(role=r,event_ids=[ep['events'][i]['id']],reason='Source') for r,i in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],question='What next?',review_status='pending')
    brief=dict(selected_ids=[ep['id']],episode_groups=[[ep['id']],[p['episodes'][1]['id']]],minimum=20,maximum=60,structure='continuous')
    p['pending_generation']=dict(brief=brief,groups=[dict(id='g',title='Saved draft',source_ids=[ep['id']],variations=[v])]);server.save(p)
    before=copy.deepcopy(server.load(p['id']))
    monkeypatch.setitem(server.state,'key','')
    def forbidden(*args):raise AssertionError('Viewing output must not use AI')
    monkeypatch.setattr(server,'call',forbidden)
    r=c.get('/api/projects/'+p['id']+'/saved-output');assert r.status_code==200
    result=r.json();assert result['saved_output_summary']['saved']==1 and result['saved_output_summary']['not_generated']==1
    draft=result['groups'][0]['variations'][0];assert draft['validation']['score'] is None and draft['validation']['eligible']
    assert server.load(p['id'])==before
    exported=c.get('/api/projects/'+p['id']+'/export?saved=true').json()
    assert exported['ranked_variations']==[] and exported['unranked_variations'][0]['id']=='pending'
    handoff=c.get('/api/projects/'+p['id']+'/handoff?saved=true')
    assert handoff.status_code==200 and 'Saved draft' in handoff.text and 'review has not completed' in handoff.text

def test_saved_reviewed_results_keep_checks(studio):
    from saved_output import view
    p,_,_=studio;ep=p['episodes'][0]
    v=dict(id='reviewed',angle='Saved',segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[dict(role=r,event_ids=[ep['events'][i]['id']],reason='Source') for r,i in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],question='What next?',review_status='reviewed',narrative=dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),rubric=[dict(criterion=k,score=4,reason='Clear') for k in server.validate_cut.__globals__['WEIGHTS']])
    p['pending_generation']=dict(brief=dict(selected_ids=[ep['id']],minimum=20,maximum=60,structure='continuous'),groups=[dict(title='Saved',source_ids=[ep['id']],variations=[v])])
    result=view(p)
    assert result['saved_output_summary']['eligible']==1 and result['groups'][0]['variations'][0]['validation']['score']==80
