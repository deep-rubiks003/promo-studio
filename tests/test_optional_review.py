import copy
from test_new_workflow import studio,finish
from test_rejected_repair import draft,review_result
import server

def test_generation_is_one_call_until_review_button(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0];calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        return dict(title='Draft',promo=draft(ep)) if stage=='candidate-generation' else review_result()
    monkeypatch.setattr(server,'call',model)
    jid=c.post('/api/projects/'+p['id']+'/generate',headers=h,json=dict(selected_ids=[ep['id']])).json()['job_id']
    assert finish(c,jid)['status']=='done'
    saved=server.load(p['id']);v=saved['groups'][0]['variations'][0]
    assert calls==['candidate-generation'] and v['validation']['eligible']
    assert v['validation']['score'] is None and v['review_status']=='unreviewed'
    assert not {'Standalone story & unresolved ending','Complete editorial review'} & {x['name'] for x in v['validation']['checks']}
    before=copy.deepcopy(v['segments'])
    jid=c.post('/api/projects/'+p['id']+'/virality-review',headers=h,json=dict(variation_id=v['id'])).json()['job_id']
    assert finish(c,jid)['status']=='done'
    v=server.load(p['id'])['groups'][0]['variations'][0]
    assert calls==['candidate-generation','independent-clip-review']
    assert v['validation']['rubric_score']==80 and v['segments']==before

def test_invalid_cut_cannot_spend_on_review(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0];v=draft(ep,end=99);v['id']='v'
    p.update(brief=server.build_brief(p,dict(selected_ids=[ep['id']])),groups=[dict(source_ids=[ep['id']],variations=[v])]);server.save(p)
    monkeypatch.setattr(server,'call',lambda *args: (_ for _ in ()).throw(AssertionError('No paid call allowed')))
    r=c.post('/api/projects/'+p['id']+'/virality-review',headers=h,json=dict(variation_id='v'))
    assert r.status_code==400


def test_discovery_pauses_until_explicit_review():
    from test_discovery import project,plan,draft as candidate,response,review_inputs
    from discovery import new_run,run_discovery
    p=project();pl=plan(p);run=new_run(pl);calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        if stage=='discovery-scan':return dict(candidates=[candidate(p['episodes'][0])],opportunities=[])
        assert stage=='discovery-review'
        return response(review_inputs(prompt))
    run_discovery(p,run,model,lambda p:None,lambda x:x,review_enabled=False)
    assert calls==['discovery-scan'] and run['phase']=='awaiting_review'
    assert len(run['shortlist'])==1 and 'posting_priority' not in run['shortlist'][0]
    assert run['shortlist'][0]['variations'][0]['validation']['score'] is None
    run_discovery(p,run,model,lambda p:None,lambda x:x,review_enabled=True)
    assert calls==['discovery-scan','discovery-review']
    assert run['shortlist'][0]['posting_priority']==1
    assert run['shortlist'][0]['variations'][0]['validation']['score']==80


def test_editorial_failure_is_separate_from_local_cut_checks():
    from parser import validate_cut
    from test_core import SCRIPT
    from parser import parse_episode
    ep=parse_episode('one.txt',SCRIPT);v=draft(ep);v.update(review_result(),review_status='reviewed')
    v['narrative']['standalone']=False
    result=validate_cut(v,[ep],[ep['id']])
    assert result['eligible'] and result['editorial_passed'] is False
    assert result['rubric_score']==80


def test_failed_optional_review_preserves_previous_results(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0];v=draft(ep);v.update(review_result(),id='v',review_status='reviewed')
    p.update(brief=server.build_brief(p,dict(selected_ids=[ep['id']])),groups=[dict(source_ids=[ep['id']],variations=[v])]);server.save(p)
    before=copy.deepcopy(server.load(p['id']))
    monkeypatch.setattr(server,'call',lambda *args:dict(narrative={},rubric=[]))
    jid=c.post('/api/projects/'+p['id']+'/virality-review',headers=h,json=dict(variation_id='v')).json()['job_id']
    assert finish(c,jid)['status']=='error'
    assert server.load(p['id'])==before
