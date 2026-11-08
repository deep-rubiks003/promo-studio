import copy
import pytest
from test_new_workflow import studio,finish
from test_rejected_repair import draft,review_result
import server
from parser import validate_cut


def saved(studio,ends=(99,99)):
    p,c,h=studio;eps=p['episodes'][:len(ends)]
    p['brief']=server.build_brief(p,dict(selected_ids=[e['id'] for e in eps],promo_mode='multiple'))
    p['groups']=[dict(id='g'+str(i),title='Draft',source_ids=[e['id']],variations=[dict(draft(e,end=end),id='v'+str(i))]) for i,(e,end) in enumerate(zip(eps,ends))]
    server.save(p)
    return p,c,h


def test_only_five_local_checks_and_optional_editorial(studio):
    p,_,_=studio;ep=p['episodes'][0];v=draft(ep);v['beats']=v['beats'][:1]
    result=validate_cut(v,[ep],[ep['id']])
    assert result['eligible'] and len(result['checks'])==5 and result['score'] is None
    assert [c['name'] for c in result['checks']]==['Source selection','Cut structure','20–60 second duration','No dialogue cut mid-line','Hook in first 3 seconds']
    assert len(result['evidence'])==4
    v.update(review_result(),review_status='reviewed');v['narrative']['standalone']=False
    assert validate_cut(v,[ep],[ep['id']])['eligible']


def test_adjacent_same_speaker_blocks_can_separate():
    ep=dict(id='e',duration=45,events=[dict(id='a',start=0,end=20,speaker='A',kind='dialogue',text='unfinished'),dict(id='b',start=20,end=45,speaker='A',kind='dialogue',text='continued')])
    v=dict(segments=[dict(episode_id='e',start=20,end=45)],beats=[dict(role='Hook',event_ids=['b'])])
    assert validate_cut(v,[ep],['e'])['eligible']
    v['segments'][0]['start']=21
    assert not validate_cut(v,[ep],['e'])['eligible']


def test_generation_review_is_explicit(studio,monkeypatch):
    p,c,h=studio;ep=p['episodes'][0];calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        return dict(title='Draft',promo=draft(ep)) if stage=='candidate-generation' else review_result()
    monkeypatch.setattr(server,'call',model)
    jid=c.post('/api/projects/'+p['id']+'/generate',headers=h,json=dict(selected_ids=[ep['id']])).json()['job_id']
    assert finish(c,jid)['status']=='done'
    v=server.load(p['id'])['groups'][0]['variations'][0]
    assert calls==['candidate-generation'] and v['validation']['score'] is None
    jid=c.post('/api/projects/'+p['id']+'/virality-review',headers=h,json=dict(variation_id=v['id'])).json()['job_id']
    assert finish(c,jid)['status']=='done'
    assert calls==['candidate-generation','independent-clip-review']
    assert server.load(p['id'])['groups'][0]['variations'][0]['validation']['score']==80


def test_manual_bulk_scoped_proposals_continue_failure(studio,monkeypatch):
    p,c,h=saved(studio);calls=[];before=copy.deepcopy(p['groups'])
    def model(pid,stage,prompt):
        calls.append(prompt);ep=p['episodes'][len(calls)-1]
        assert stage=='manual-repair-proposal' and 'UNSELECTED_SECRET' not in prompt
        assert p['episodes'][1 if len(calls)==1 else 0]['id'] not in prompt
        if len(calls)==1:raise ValueError('Malformed JSON')
        return dict(variation=dict(draft(ep),distinction='Cut'),change='Fixed duration',tradeoff='Less context')
    monkeypatch.setattr(server,'call',model)
    jid=c.post('/api/projects/'+p['id']+'/repairs',headers=h,json=dict(variation_ids=['v0','v1'])).json()['job_id']
    assert finish(c,jid)['status']=='done'
    out=server.load(p['id']);assert out['groups']==before and set(out['proposals'])=={'v1'}
    assert [i['status'] for i in out['manual_repair_batch']['items']]==['failed','proposed']
    assert out['proposals']['v1']['variation']['validation']['score'] is None
    assert c.post('/api/projects/'+p['id']+'/apply',headers=h,json=dict(variation_id='v1')).status_code==200
    assert server.load(p['id'])['groups'][1]['variations'][0]['review_status']=='unreviewed'


def test_invalid_repair_selection_never_calls(studio,monkeypatch):
    p,c,h=saved(studio,ends=(25,99));monkeypatch.setattr(server,'call',lambda *a:pytest.fail('No paid request'))
    assert c.post('/api/projects/'+p['id']+'/repairs',headers=h,json=dict(variation_ids=['v0','v1'])).status_code==400


def test_bulk_quota_preserves_first_proposal_and_stops(studio,monkeypatch):
    p,c,h=saved(studio);calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        if len(calls)>1:raise server.ProviderError(429,'RESOURCE_EXHAUSTED','Quota exhausted')
        return dict(variation=dict(draft(p['episodes'][0]),distinction='Cut'),change='Fixed',tradeoff='Less context')
    monkeypatch.setattr(server,'call',model)
    jid=c.post('/api/projects/'+p['id']+'/repairs',headers=h,json=dict(variation_ids=['v0','v1'])).json()['job_id']
    assert finish(c,jid)['status']=='error'
    out=server.load(p['id']);assert list(out['proposals'])==['v0'] and out['manual_repair_batch']['status']=='paused'


def test_discard_invalid_current_clears_stale_review(studio):
    p,c,h=saved(studio,ends=(99,));v=p['groups'][0]['variations'][0];v.update(review_result(),review_status='reviewed')
    p['proposals']={'v0':dict(variation=dict(draft(p['episodes'][0]),id='v0'))};server.save(p)
    r=c.post('/api/projects/'+p['id']+'/discard',headers=h,json=dict(variation_id='v0'))
    assert r.status_code==200
    v=r.json()['groups'][0]['variations'][0]
    assert not v['validation']['eligible'] and v['validation']['score'] is None and 'rubric' not in v
    assert any('Kept the current cut' in w for w in v['validation']['warnings'])


def test_optional_review_failure_preserves_previous_assessment(studio,monkeypatch):
    p,c,h=saved(studio,ends=(25,));v=p['groups'][0]['variations'][0];v.update(review_result(),review_status='reviewed');server.save(p)
    before=copy.deepcopy(server.load(p['id']))
    monkeypatch.setattr(server,'call',lambda *a:dict(narrative={},rubric=[]))
    jid=c.post('/api/projects/'+p['id']+'/virality-review',headers=h,json=dict(variation_id='v0')).json()['job_id']
    assert finish(c,jid)['status']=='error' and server.load(p['id'])==before


def test_cancelled_bulk_preserves_completed_proposal(studio,monkeypatch):
    import time
    p,c,h=saved(studio);calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        if len(calls)==2:
            server.context.runtime.cancelled.set()
            raise server.JobCancelled('Stopped by you')
        return dict(variation=dict(draft(p['episodes'][0]),distinction='Cut'),change='Fixed',tradeoff='Less context')
    monkeypatch.setattr(server,'call',model)
    jid=c.post('/api/projects/'+p['id']+'/repairs',headers=h,json=dict(variation_ids=['v0','v1'])).json()['job_id']
    for _ in range(200):
        job=c.get('/api/jobs/'+jid).json()
        if job['status'] in server.TERMINAL:break
        time.sleep(.01)
    assert job['status']=='cancelled'
    out=server.load(p['id']);assert list(out['proposals'])==['v0'] and out['manual_repair_batch']['status']=='paused'
    assert out['manual_repair_batch']['items'][1]['status']=='paused'


@pytest.mark.parametrize('keep_new',[False,True])
def test_failed_repair_choice_can_run_optional_review(studio,monkeypatch,keep_new):
    p,c,h=saved(studio,ends=(99,));ep=p['episodes'][0]
    proposal=dict(draft(ep,end=98),id='v0');p['proposals']={'v0':dict(variation=proposal)};server.save(p)
    calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        assert 'Local cut-check warnings' in prompt
        return review_result()
    monkeypatch.setattr(server,'call',model)
    result=c.post('/api/projects/'+p['id']+('/apply' if keep_new else '/discard'),headers=h,json=dict(variation_id='v0'))
    assert result.status_code==200
    current=server.load(p['id'])['groups'][0]['variations'][0]
    assert not current['validation']['eligible']
    assert current['segments'][0]['end']==(98 if keep_new else 99)
    response=c.post('/api/projects/'+p['id']+'/virality-review',headers=h,json=dict(variation_id='v0'))
    assert response.status_code==200 and finish(c,response.json()['job_id'])['status']=='done'
    current=c.get('/api/projects/'+p['id']).json()['groups'][0]['variations'][0]
    assert calls==['independent-clip-review'] and current['validation']['rubric_score']==80
    assert not current['validation']['eligible'] and current['review_status']=='reviewed'
