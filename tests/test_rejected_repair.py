import copy
import pytest
from test_new_workflow import studio,finish
import server
from workflow import normalize_sources

def draft(ep,end=25,ref=None):
    return dict(angle='Letter',segments=[dict(episode_id=ep['id'] if ref is None else ref,start=0,end=end)],beats=[dict(role=r,event_ids=[ep['events'][i]['id']],reason='Source') for r,i in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],question='Why is she outside?')

def review_result():
    return dict(narrative=dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),rubric=[dict(criterion=k,score=4,reason='Clear') for k in server.validate_cut.__globals__['WEIGHTS']],review_notes='Clear')

def test_alias_resolution_is_selected_and_unambiguous(studio):
    p,_,_=studio;a,b=p['episodes'][:2]
    v=draft(a,ref=a['number']);normalize_sources(v,p['episodes'],[a['id']]);assert v['segments'][0]['episode_id']==a['id']
    v=draft(a,ref=b['number']);normalize_sources(v,p['episodes'],[a['id']]);assert v['segments'][0]['episode_id']==b['number']
    duplicate=copy.deepcopy(a);duplicate['id']='duplicate';v=draft(a,ref=a['number']);normalize_sources(v,[a,duplicate],[a['id'],'duplicate']);assert v['segments'][0]['episode_id']==a['number']

def test_bad_source_skips_paid_review(studio,monkeypatch):
    p,_,_=studio;ep=p['episodes'][0];v=draft(ep,end=64.3,ref=ep['number'])
    monkeypatch.setattr(server,'call',lambda *args:pytest.fail('Invalid source must never reach paid review'))
    server.review(p,v,dict(selected_ids=[ep['id']],minimum=20,maximum=60,structure='continuous'))
    assert v['review_status']=='needs_repair' and not v['validation']['eligible'] and v['validation']['score'] is None
    assert v['segments'][0]['episode_id']==ep['id']

def test_resume_revalidates_saved_draft_without_paid_review(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0];v=draft(ep,ref=ep['number']);v.update(review_status='reviewed',narrative=dict(standalone=False),rubric=[],validation=dict(eligible=False,checks=[dict(name='Source selection',passed=False,detail='Invalid reference')]))
    brief=server.build_brief(p,dict(selected_ids=[ep['id']]))
    p['pending_generation']=dict(brief=brief,groups=[dict(id='g',title='Saved',source_ids=[ep['id']],variations=[v])]);server.save(p)
    monkeypatch.setattr(server,'call',lambda *a:pytest.fail('Saved complete draft needs only local revalidation'))
    jid=c.post('/api/projects/'+p['id']+'/resume',headers=h,json={}).json()['job_id']
    assert finish(c,jid)['status']=='done'
    saved=server.load(p['id']);v=saved['groups'][0]['variations'][0]
    assert v['validation']['eligible'] and v['segments'][0]['episode_id']==ep['id']
    assert v['validation']['score'] is None


def test_repairs_are_bounded_and_failure_explained(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0];calls=[]
    def fake(pid,stage,prompt):
        calls.append(stage);assert stage!='independent-clip-review'
        return dict(title='Bad range',promo=draft(ep,end=64.3,ref=ep['number']))
    monkeypatch.setattr(server,'call',fake)
    jid=c.post('/api/projects/'+p['id']+'/generate',headers=h,json={'selected_ids':[ep['id']]}).json()['job_id']
    result=finish(c,jid)
    assert result['status']=='done'
    assert calls==['candidate-generation']
    saved=server.load(p['id']);assert 'pending_generation' not in saved and not saved['groups'][0]['variations'][0]['validation']['eligible']
    assert saved['generation_summary']['needs_repair']==1


def test_batch_continues_after_rejection_and_publishes_both(studio,monkeypatch):
    p,c,h=studio;eps=p['episodes'][:2];calls=[]
    def fake(pid,stage,prompt):
        calls.append(stage)
        if stage=='candidate-generation':
            ep=next(ep for ep in eps if ep['id'] in prompt)
            return dict(title='Promo '+str(ep['number']),promo=draft(ep,end=64.3 if ep==eps[0] else 25))
        result=review_result()
        if eps[0]['id'] in prompt:
            result['narrative'].update(standalone=False,coherent=False)
            result['review_notes']='Two unrelated scenes lack a connection.'
            for row in result['rubric']:
                if row['criterion']=='Standalone clarity':row['score']=1
        return result
    monkeypatch.setattr(server,'call',fake)
    jid=c.post('/api/projects/'+p['id']+'/generate',headers=h,json=dict(selected_ids=[e['id'] for e in eps],promo_mode='multiple')).json()['job_id']
    result=finish(c,jid);assert result['status']=='done'
    saved=server.load(p['id']);assert saved['generation_summary']==dict(total=2,passed=1,needs_repair=1)
    assert len(saved['groups'])==2 and not saved['groups'][0]['variations'][0]['validation']['eligible'] and saved['groups'][1]['variations'][0]['validation']['eligible']
    v=saved['groups'][0]['variations'][0]
    assert v['validation']['score'] is None and v['validation']['rubric_score'] is None
    assert not next(c['passed'] for c in v['validation']['checks'] if c['name']=='Source selection')
    assert calls==['candidate-generation','candidate-generation']
    assert 'pending_generation' not in saved


def test_all_rejected_complete_and_preserve_previous_output(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0];previous=[dict(id='previous',title='Approved earlier',variations=[])]
    p['groups']=copy.deepcopy(previous);server.save(p)
    monkeypatch.setattr(server,'call',lambda *args:dict(title='Needs work',promo=draft(ep,end=64.3)))
    jid=c.post('/api/projects/'+p['id']+'/generate',headers=h,json=dict(selected_ids=[ep['id']])).json()['job_id']
    assert finish(c,jid)['status']=='done'
    saved=server.load(p['id']);assert saved['previous_generation']['groups']==previous and len(saved['groups'])==1
    assert saved['generation_summary']['passed']==0 and saved['generation_summary']['needs_repair']==1
