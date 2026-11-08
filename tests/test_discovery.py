"""Offline contract tests: model doubles inspect exact bounded source prompts."""
import copy
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery import (MAX_PROMPT_BYTES, CANDIDATES_PER_BATCH, plan_discovery,
                       new_run, run_discovery)
from parser import WEIGHTS
from prompt_payload import unpack_events


def episode(n, count=12, text='The secret is still unresolved.'):
    eid=f'ep{n}'
    return dict(id=eid, number=n, title=f'Episode {n}', filename=f'{n}.txt',
                duration=count*5, logline='', warnings=[], events=[
                    dict(id=f'{eid}:{i}', start=i*5, end=(i+1)*5,
                         kind='dialogue', speaker=f'Speaker {i%2}', text=text)
                    for i in range(count)])


def project(n=1, **kwargs):
    return dict(id='p', episodes=[episode(i+1, **kwargs) for i in range(n)],
                groups=[{'saved':'manual'}], brief={'saved':'brief'}, proposals={'saved':True})


def plan(p, **kwargs):
    return plan_discovery(p,dict(selected_ids=[e['id'] for e in p['episodes']], **kwargs),'gemini-3.5-flash-lite')


def draft(ep, start=0, score=4):
    ev=[e for e in ep['events'] if start<=e['start'] and e['end']<=start+25]
    return dict(angle='A secret', distinction='One conflict',
                segments=[dict(episode_id=ep['id'],start=start,end=start+25)],
                beats=[dict(role=role,event_ids=[ev[index]['id']],reason='Exact source')
                       for role,index in zip(['Hook','Context','Escalation','Cliffhanger'],[0,1,2,4])],
                question='Who will reveal the secret?', rough_priority=score)


def response(inputs, score=4, standalone=True):
    return dict(results=[dict(promo_id=x['promo_id'],
        narrative=dict(standalone=standalone,escalation=True,cliffhanger=True,coherent=True),
        rubric=[dict(criterion=k,score=score,reason='Visible in clip') for k in WEIGHTS],
        review_notes='Newcomer assessment') for x in inputs])


def review_inputs(prompt):
    return unpack_events(json.loads(prompt.split('CLIPS:',1)[1]))


def execute(p, pl, call, run=None):
    run=run or new_run(pl)
    p.setdefault('discovery_runs',[]).append(run)
    saves=[]
    run_discovery(p,run,call,lambda value:saves.append(copy.deepcopy(value)),lambda eps:eps)
    return run,saves


@pytest.mark.parametrize('count',[50,100])
def test_pack_coverage_and_request_bounds(count):
    p=project(count)
    pl=plan(p)
    assert set(e['id'] for ep in p['episodes'] for e in ep['events'])==set(e['id'] for w in pl['windows'] for e in w['events'])
    assert sorted(w['id'] for w in pl['windows'])==sorted(wid for b in pl['batches'] for wid in b['window_ids'])
    calls=[]
    def model(pid,stage,prompt):
        calls.append(prompt)
        assert len(prompt.encode('utf-8'))<=MAX_PROMPT_BYTES
        return dict(candidates=[],opportunities=[])
    run,saves=execute(p,pl,model)
    assert len(calls)==len(pl['batches'])
    assert pl['estimated_calls']['minimum']<=len(calls)<=pl['estimated_calls']['maximum']
    assert run['status']=='done' and all(c['status']=='no_candidate' for c in run['coverage'])
    assert len(run['coverage'])==len(pl['windows']) and saves
    assert p['groups']==[{'saved':'manual'}] and p['proposals']=={'saved':True}


def test_long_windows_keep_entire_overlapping_turns_and_utf8():
    p=project(count=160,text='हिंदी संवाद '*50)
    p['episodes'][0]['events'][12]['end']=130
    pl=plan(p)
    assert len(pl['windows'])>1
    actual={e['id']:e for w in pl['windows'] for e in w['events']}
    assert actual=={e['id']:e for e in p['episodes'][0]['events']}
    assert all(e in p['episodes'][0]['events'] for w in pl['windows'] for e in w['events'])
    assert any(set(e['id'] for e in a['events'])&set(e['id'] for e in b['events']) for a,b in zip(pl['windows'],pl['windows'][1:]))


@pytest.mark.parametrize('body',[
    {'selected_ids':[]},{'selected_ids':['ep1','ep1']},{'selected_ids':[{}]},
    {'selected_ids':[True]},{'selected_ids':['missing']},
    {'minimum':math.nan},{'maximum':math.inf},{'minimum':True},
    {'limit':True},{'limit':0},{'limit':31},{'structure':'montage'},{'quantity':'exact'},
])
def test_invalid_scope_and_limits_fail_before_calls(body):
    p=project()
    request=dict(selected_ids=['ep1']);request.update(body)
    with pytest.raises(ValueError):plan_discovery(p,request,'gemini-3.5-flash-lite')


def test_oversize_single_turn_is_explicitly_rejected_without_truncation():
    with pytest.raises(ValueError,match='(?i)(large|budget|size|long)'):
        plan(project(count=1,text='x'*MAX_PROMPT_BYTES))


def test_source_checks_precede_review_and_model_approvals_are_stripped():
    p=project();good=draft(p['episodes'][0]);bad=copy.deepcopy(good)
    bad['segments'][0]['start']=1
    bad.update(narrative={k:True for k in ['standalone','escalation','cliffhanger','coherent']},
               rubric=[dict(criterion=k,score=5) for k in WEIGHTS],review_status='reviewed')
    stages=[]
    def model(pid,stage,prompt):
        stages.append(stage)
        if stage=='discovery-scan':return dict(candidates=[bad],opportunities=[])
        pytest.fail('Invalid source must not incur independent review')
    run,_=execute(p,plan(p),model)
    assert len(stages)==1 and not run['shortlist'] and run['rejections']


def test_rank_only_independently_reviewed_standalone_candidates_and_dedup():
    p=project(2);a=draft(p['episodes'][0]);b=draft(p['episodes'][1]);duplicate=copy.deepcopy(a)
    def model(pid,stage,prompt):
        if stage=='discovery-scan':return dict(candidates=[a,duplicate,b],opportunities=[])
        inputs=review_inputs(prompt)
        rows=response(inputs)
        for row,item in zip(rows['results'],inputs):
            if item['segments'][0]['episode_id']=='ep1':row['narrative']['standalone']=False
        return rows
    run,_=execute(p,plan(p),model)
    assert len(run['shortlist'])==1
    assert run['shortlist'][0]['source_ids']==['ep2']
    assert run['shortlist'][0]['variations'][0]['validation']['eligible']
    assert any('duplicate' in str(r).lower() for r in run['rejections'])


def test_cap_is_up_to_count_and_reviews_are_clip_only():
    p=project(2);drafts=[draft(ep) for ep in p['episodes']]
    p['episodes'][0]['events'][-1]['text']='UNSEEN_GENERATOR_CONTEXT'
    def model(pid,stage,prompt):
        if stage=='discovery-scan':return dict(candidates=drafts,opportunities=[])
        assert 'UNSEEN_GENERATOR_CONTEXT' not in prompt
        items=review_inputs(prompt)
        assert 1<=len(items)<=5 and len(prompt.encode())<=MAX_PROMPT_BYTES
        return response(items)
    run,_=execute(p,plan(p,quantity='cap',limit=1),model)
    assert len(run['shortlist'])==1
    p2=project()
    run,_=execute(p2,plan(p2,quantity='cap',limit=20),lambda *args:dict(candidates=[],opportunities=[]))
    assert run['shortlist']==[]


def test_completed_scan_and_reviews_cached_across_runs_but_model_isolation():
    p=project();calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        return dict(candidates=[draft(p['episodes'][0])],opportunities=[]) if stage=='discovery-scan' else response(review_inputs(prompt))
    first,_=execute(p,plan(p),model)
    assert len(calls)==2
    second,_=execute(p,plan(p,quantity='cap',limit=2),model)
    assert len(calls)==2 and second['shortlist']
    changed=plan_discovery(p,dict(selected_ids=['ep1']),'gemini-3.1-flash-lite')
    execute(p,changed,model)
    assert len(calls)==4


def test_resume_after_scan_failure_preserves_completed_coverage():
    p=project(100);pl=plan(p);run=new_run(pl);p['discovery_runs']=[run];calls=[]
    def failing(pid,stage,prompt):
        calls.append(prompt)
        if len(calls)==2:raise RuntimeError('offline interruption')
        return dict(candidates=[],opportunities=[])
    with pytest.raises(RuntimeError):run_discovery(p,run,failing,lambda p:None,lambda eps:eps)
    completed=sum(c['status']!='pending' for c in run['coverage'])
    assert completed>0
    retry=[]
    run_discovery(p,run,lambda *args:(retry.append(args) or dict(candidates=[],opportunities=[])),lambda p:None,lambda eps:eps)
    assert len(retry)==len(pl['batches'])-1 and run['status']=='done'


def test_review_id_coverage_failure_is_atomic_and_resume_uses_scan_cache():
    p=project(2);pl=plan(p);run=new_run(pl);p['discovery_runs']=[run];stages=[]
    def bad(pid,stage,prompt):
        stages.append(stage)
        if stage=='discovery-scan':return dict(candidates=[draft(ep) for ep in p['episodes']],opportunities=[])
        return dict(results=[])
    with pytest.raises(ValueError,match='(?i)(every|complete|exactly|coverage)'):
        run_discovery(p,run,bad,lambda p:None,lambda eps:eps)
    assert all(v['review_status']=='pending' for v in run['candidates'])
    def retry(pid,stage,prompt):
        assert stage!='discovery-scan'
        return response(review_inputs(prompt))
    run_discovery(p,run,retry,lambda p:None,lambda eps:eps)
    assert len(run['shortlist'])==2


def test_insufficient_opportunities_never_force_stitch_request():
    p=project();stages=[]
    def model(pid,stage,prompt):
        stages.append(stage);return dict(candidates=[],opportunities=[])
    run,_=execute(p,plan(p,structure='either'),model)
    assert stages==['discovery-scan'] and not run['shortlist']


def test_short_source_batches_keep_candidate_capacity_and_camera_cues():
    p=project(100)
    p['episodes'][0]['events'][0].update(kind='camera',text='VISUAL_HOOK_CAMERA')
    pl=plan(p)
    assert all(len(batch['window_ids'])<=5 for batch in pl['batches'])
    assert len(pl['batches'])>=20
    prompts=[]
    execute(p,pl,lambda pid,stage,prompt:(prompts.append(prompt) or dict(candidates=[],opportunities=[])))
    assert any('VISUAL_HOOK_CAMERA' in prompt for prompt in prompts)


def test_linked_cross_batch_opportunities_get_at_most_one_exact_source_stitch():
    p=project(8);pl=plan(p,structure='either');stages=[]
    count=0
    def model(pid,stage,prompt):
        nonlocal count
        stages.append(stage)
        if stage=='discovery-scan':
            batch=pl['batches'][count];count+=1
            lookup={w['id']:w for w in pl['windows']}
            event=lookup[batch['window_ids'][0]]['events'][0]['id']
            return dict(candidates=[],opportunities=[dict(key='Shared secret',reason='Potential connection',event_ids=[event])])
        assert stage=='discovery-stitch'
        assert 'EXACT SOURCE WINDOWS:' in prompt and 'LINKED OPPORTUNITIES:' in prompt
        source=unpack_events(json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1]))
        assert all(e in next(ep for ep in p['episodes'] if ep['id']==w['episode_id'])['events'] for w in source for e in w['events'])
        return dict(candidates=[],opportunities=[])
    run,_=execute(p,pl,model)
    assert stages.count('discovery-stitch')==1 and run['shortlist']==[]


def test_completed_review_batch_survives_later_review_failure():
    p=project(12);pl=plan(p);run=new_run(pl);p['discovery_runs']=[run];scan=0;reviews=0
    def model(pid,stage,prompt):
        nonlocal scan,reviews
        if stage=='discovery-scan':
            eps=p['episodes'][scan*5:scan*5+3];scan+=1
            return dict(candidates=[draft(ep) for ep in eps],opportunities=[])
        reviews+=1
        if reviews==2:raise RuntimeError('review interrupted')
        return response(review_inputs(prompt))
    with pytest.raises(RuntimeError):run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert run['review_cursor']==5
    before=copy.deepcopy(run['candidates'][:5]);remaining=[]
    def resume(pid,stage,prompt):
        assert stage=='discovery-review'
        remaining.extend(review_inputs(prompt));return response(review_inputs(prompt))
    run_discovery(p,run,resume,lambda p:None,lambda eps:eps)
    assert run['candidates'][:5]==before and len(remaining)==3


def test_reviewed_but_editorially_weak_cut_does_not_fill_cap():
    p=project()
    def model(pid,stage,prompt):
        return dict(candidates=[draft(p['episodes'][0])],opportunities=[]) if stage=='discovery-scan' else response(review_inputs(prompt),score=2)
    run,_=execute(p,plan(p,quantity='cap',limit=10),model)
    assert run['candidates'][0]['validation']['eligible']
    assert run['shortlist']==[]
    assert 'heuristic' in run['summary']['shortlist_policy'].lower()


def test_auto_stops_when_repeated_sources_drop_marginal_quality_below_floor():
    p=project(count=20)
    def model(pid,stage,prompt):
        return dict(candidates=[draft(p['episodes'][0],start=t) for t in (0,30,60)],opportunities=[]) if stage=='discovery-scan' else response(review_inputs(prompt),score=3.6)
    automatic,_=execute(p,plan(p,quantity='auto'),model)
    capped,_=execute(p,plan(p,quantity='cap',limit=3),model)
    assert len(automatic['shortlist'])==1 and len(capped['shortlist'])==3
    assert 'Hook:' in automatic['shortlist'][0]['posting_reason']
    assert 'Cliffhanger:' in automatic['shortlist'][0]['posting_reason']
    assert '3.0' in capped['shortlist'][1]['posting_reason']


def test_near_overlap_waits_for_review_so_untrusted_priority_cannot_discard_good_cut():
    p=project(count=8)
    # 25/30 seconds overlap: near duplicates, but the longer cut lacks clarity.
    strong=draft(p['episodes'][0]);weak=copy.deepcopy(strong)
    weak['segments'][0]['end']=30
    weak['rough_priority']=100
    weak['angle']='Needs unseen context'
    reviewed=[]
    def model(pid,stage,prompt):
        if stage=='discovery-scan':return dict(candidates=[weak,strong],opportunities=[])
        clips=review_inputs(prompt);reviewed.extend(clips)
        result=response(clips)
        for item,row in zip(clips,result['results']):
            if item['segments'][0]['end']==30:row['narrative']['standalone']=False
        return result
    run,_=execute(p,plan(p,quantity='cap',limit=3),model)
    assert len(reviewed)==2
    assert len(run['shortlist'])==1 and run['shortlist'][0]['variations'][0]['segments'][0]['end']==25


def test_post_review_near_overlap_uses_independent_quality_and_partial_shortlist_is_saved():
    p=project(count=8);a=draft(p['episodes'][0]);b=copy.deepcopy(a)
    b['segments'][0]['end']=30;b['rough_priority']=99
    pl=plan(p,quantity='cap',limit=3);run=new_run(pl);p['discovery_runs']=[run]
    saves=[]
    def model(pid,stage,prompt):
        if stage=='discovery-scan':return dict(candidates=[b,a],opportunities=[])
        clips=review_inputs(prompt);result=response(clips)
        for item,row in zip(clips,result['results']):
            if item['segments'][0]['end']==25:
                for criterion in row['rubric']:criterion['score']=4.8
        return result
    run_discovery(p,run,model,lambda value:saves.append(copy.deepcopy(run)),lambda eps:eps)
    assert len(run['shortlist'])==1 and run['shortlist'][0]['variations'][0]['segments'][0]['end']==25
    assert any(s.get('shortlist') and s.get('summary',{}).get('complete') is False for s in saves)
    assert run['summary']['complete'] is True


def test_different_episode_playback_order_is_not_pre_review_duplicate():
    p=project(2,count=5)
    def stitched(order):
        seq=[p['episodes'][i] for i in order]
        v=draft(seq[0]);v['segments']=[dict(episode_id=ep['id'],start=0,end=25) for ep in seq]
        v['beats']=[dict(role=role,event_ids=[ep['events'][idx]['id']],reason='Source') for role,ep,idx in [
            ('Hook',seq[0],0),('Context',seq[0],1),('Escalation',seq[1],0),('Cliffhanger',seq[1],4)]]
        return v
    def model(pid,stage,prompt):
        return dict(candidates=[stitched([0,1]),stitched([1,0])],opportunities=[]) if stage=='discovery-scan' else response(review_inputs(prompt),score=5)
    run,_=execute(p,plan(p,structure='stitched',quantity='cap',limit=3),model)
    assert len(run['shortlist'])==2
