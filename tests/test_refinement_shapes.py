import copy
import pytest
from test_new_workflow import studio,finish
from test_rejected_repair import draft,review_result
from workflow import normalize_refinement
import server

def setup_draft(p):
    ep=p['episodes'][0];v=draft(ep);v.update(id='original',distinction='Original intent',reason='Conflict',weakness='Timing',review_status='reviewed',**review_result())
    brief=server.build_brief(p,dict(selected_ids=[ep['id']]))
    v['validation']=server.validate_cut(v,p['episodes'],[ep['id']],20,60,'continuous')
    p.update(brief=brief,groups=[dict(id='group',title='Letter',source_ids=[ep['id']],variations=[v])]);server.save(p)
    return ep,v

def proposal(ep):
    v=draft(ep);v.update(angle='New opening',distinction='Stronger opening',reason='Conflict',weakness='Less setup')
    v['segments'][0]['start']=5
    for beat,i in zip(v['beats'],[1,1,2,2]):beat['event_ids']=[ep['events'][i]['id']]
    return v

@pytest.mark.parametrize('layout',['nested','flattened'])
def test_refinement_proposal_review_apply(studio,monkeypatch,layout):
    p,c,h=studio;ep,old=setup_draft(p);new=proposal(ep);calls=[]
    raw=dict(change='Start later',tradeoff='Less setup')
    raw.update(dict(variation=new) if layout=='nested' else dict(variation='Stronger opening',**new))
    original=copy.deepcopy(raw)
    def fake(pid,stage,prompt):
        calls.append(stage);assert 'UNSELECTED_SECRET' not in prompt
        if stage=='refinement-proposal':
            assert 'variation MUST be a nested OBJECT' in prompt
            return raw
        assert 'Your sister has it.' in prompt
        return review_result()
    monkeypatch.setattr(server,'call',fake)
    jid=c.post('/api/projects/'+p['id']+'/refine',headers=h,json=dict(variation_id=old['id'],part='Hook',prompt='Stronger opening')).json()['job_id']
    result=finish(c,jid);assert result['status']=='done',result
    saved=server.load(p['id']);assert saved['groups'][0]['variations'][0]==old
    assert saved['proposals'][old['id']]['variation']['validation']['eligible']
    assert raw==original
    applied=c.post('/api/projects/'+p['id']+'/apply',headers=h,json=dict(variation_id=old['id']))
    assert applied.status_code==200 and applied.json()['groups'][0]['variations'][0]['segments'][0]['start']==5
    assert calls==['refinement-proposal']
    assert applied.json()['groups'][0]['variations'][0]['validation']['score'] is None

@pytest.mark.parametrize('response',[
    dict(variation='Just a description',change='New',tradeoff='Less'),
    dict(error='No romantic source moments are present.'),
    dict(variation={'segments':[]},change='New',tradeoff='Less')
])
def test_unusable_refinement_keeps_current_and_existing_proposal(studio,monkeypatch,response):
    p,c,h=studio;ep,old=setup_draft(p)
    p['proposals'][old['id']]=dict(variation=proposal(ep),change='Prior suggestion',tradeoff='Less setup');server.save(p)
    before=copy.deepcopy(server.load(p['id']));calls=[]
    def fake(pid,stage,prompt):calls.append(stage);return response
    monkeypatch.setattr(server,'call',fake)
    jid=c.post('/api/projects/'+p['id']+'/refine',headers=h,json=dict(variation_id=old['id'],part='Hook',prompt='More romantic')).json()['job_id']
    result=finish(c,jid);assert result['status']=='error' and 'current cut is unchanged' in result['error']
    assert server.load(p['id'])==before and calls==['refinement-proposal']


def test_flattened_invalid_cut_is_visible_but_cannot_apply(studio,monkeypatch):
    p,c,h=studio;ep,old=setup_draft(p);new=proposal(ep);new['segments'][0]['end']=90
    new.update(validation=dict(eligible=True),narrative=dict(standalone=True),rubric=[dict(criterion='Hook',score=5)])
    response=dict(variation='Longer version',change='Longer ending',tradeoff='Longer duration',**new)
    monkeypatch.setattr(server,'call',lambda pid,stage,prompt: response if stage=='refinement-proposal' else pytest.fail('Bad source must skip paid review'))
    jid=c.post('/api/projects/'+p['id']+'/refine',headers=h,json=dict(variation_id=old['id'],part='Whole promo',prompt='Longer')).json()['job_id']
    assert finish(c,jid)['status']=='done'
    saved=server.load(p['id']);v=saved['proposals'][old['id']]['variation']
    assert not v['validation']['eligible'] and not v['rubric']
    assert c.post('/api/projects/'+p['id']+'/apply',headers=h,json=dict(variation_id=old['id'])).status_code==400
    assert server.load(p['id'])['groups'][0]['variations'][0]==old
