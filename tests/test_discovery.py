"""Offline contract tests: model doubles inspect exact bounded source prompts."""
import copy
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery import (MAX_PROMPT_BYTES, CANDIDATES_PER_BATCH, plan_discovery,
                       new_run, run_discovery, project_episode_results, repair_episode, repair_episodes, decide_episode_repair, review_episode, validate_repair_selection)
from parser import WEIGHTS


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
    return json.loads(prompt.split('CLIPS:',1)[1])


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
    assert len(run['shortlist'])==2
    assert {row['source_ids'][0] for row in run['shortlist']}=={'ep1','ep2'}
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
        source=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])
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
    assert len(run['shortlist'])==1
    assert run['shortlist'][0]['variations'][0]['validation']['eligible']


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


@pytest.mark.parametrize('count',[50,100])
def test_full_show_real_sized_pack_ranks_every_qualified_cut_and_preserves_turns(count):
    # Ten-minute episodes, 120 complete turns apiece, roughly 2-4 MB per pack.
    p=project(count,count=120,text=('A complete source turn about the hidden evidence. '*3))
    pl=plan_discovery(p,dict(selected_ids=['ep1'],quantity='all'), 'gemini-3.5-flash-lite')
    assert pl['selected_ids']==[ep['id'] for ep in p['episodes']]
    assert pl['limit'] is None and pl['total_windows']>count
    assert all(len(b['window_ids'])==1 for b in pl['batches'])
    original={e['id']:e for ep in p['episodes'] for e in ep['events']}
    assert {e['id']:e for w in pl['windows'] for e in w['events']}==original
    byid={ep['id']:ep for ep in p['episodes']};calls=[];snapshots=[]
    run=new_run(pl)
    def model(pid,stage,prompt):
        calls.append(stage)
        assert len(prompt.encode())<=MAX_PROMPT_BYTES
        if stage=='discovery-scan':
            w=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]
            assert all(e==original[e['id']] for e in w['events'])
            # Exactly one strong, distinct cut per episode; later windows have none.
            return dict(candidates=[draft(byid[w['episode_id']])] if w['start']==0 else [], opportunities=[])
        return response(review_inputs(prompt),score=4)
    run_discovery(p,run,model,lambda p:snapshots.append(copy.deepcopy(run.get('summary',{}))),lambda eps:eps)
    assert run['status']=='done' and len(run['shortlist'])==count>30
    assert run['summary']['coverage_complete'] and run['summary']['scanned_events']==count*120
    assert run['summary']['batches_completed']==pl['batch_count']
    assert any(x.get('shortlisted',0)>0 and x.get('batches_completed',0)<pl['batch_count'] for x in snapshots)
    assert len(calls)<=pl['estimated_calls']['maximum']
    assert calls[:2]==['discovery-scan','discovery-review']
    for index,row in enumerate(run['shortlist']):
        assert row['posting_priority']==index+1
        assert row['ordered_source_ranges']==row['variations'][0]['segments']
        assert row['hook'] and row['cliffhanger'] and row['posting_reason']


def test_full_show_adaptive_scan_checkpoints_children_and_resume_does_not_repeat_success():
    p=project(count=24);pl=plan(p,quantity='all');run=new_run(pl)
    original=pl['windows'][0]['events'];seen=[]
    def interrupted(pid,stage,prompt):
        source=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]
        ids=tuple(e['id'] for e in source['events']);seen.append(ids)
        if len(ids)==len(original):raise ValueError('Gemini returned malformed JSON.')
        if len(seen)==3:raise RuntimeError('network interruption')
        return dict(candidates=[],opportunities=[])
    with pytest.raises(RuntimeError):run_discovery(p,run,interrupted,lambda p:None,lambda eps:eps)
    successful=seen[1];resumed=[]
    assert run['scan_splits'] and run['summary']['scanned_events']>0
    assert run['summary']['scanned_windows']==0
    def resume(pid,stage,prompt):
        ids=tuple(e['id'] for e in json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]['events'])
        resumed.append(ids);return dict(candidates=[],opportunities=[])
    run_discovery(p,run,resume,lambda p:None,lambda eps:eps)
    assert successful not in resumed and tuple(e['id'] for e in original) not in resumed
    assert run['summary']['coverage_complete']


def test_full_show_terminal_malformed_leaf_reports_gap_and_continues_other_episodes():
    p=project(2,count=4);run=new_run(plan(p,quantity='all'));calls=[]
    def model(pid,stage,prompt):
        source=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0];calls.append(source)
        if source['episode_id']=='ep1':return dict(candidates='invalid',opportunities=[])
        return dict(candidates=[],opportunities=[])
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert run['status']=='done' and run['summary']['failed_windows']==1
    assert run['summary']['scanned_events']==4 and not run['summary']['coverage_complete']
    assert len(calls)<=run['plan']['estimated_calls']['maximum']
    assert any(x.get('coverage_failure') for x in run['rejections'])


def test_full_show_malformed_review_splits_and_single_bad_cut_does_not_discard_others():
    p=project(count=20);run=new_run(plan(p,quantity='all'));calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        if stage=='discovery-scan':return dict(candidates=[draft(p['episodes'][0],start=t) for t in (0,30,60)],opportunities=[])
        clips=review_inputs(prompt)
        if len(clips)>1 or clips[0]['segments'][0]['start']==30:return dict(results=[])
        return response(clips,score=3.6)
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert len(run['shortlist'])==2 and run['summary']['failed_reviews']==1
    assert run['review_cursor']==3 and run['status']=='done'
    assert all(row['posting_priority']==i+1 for i,row in enumerate(run['shortlist']))


def test_full_show_review_failure_resume_preserves_early_ranked_cuts_without_repeated_scans():
    p=project(3);run=new_run(plan(p,quantity='all'));review_count=0;scans=[]
    def model(pid,stage,prompt):
        nonlocal review_count
        if stage=='discovery-scan':
            source=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0];scans.append(source['episode_id'])
            ep=next(ep for ep in p['episodes'] if ep['id']==source['episode_id'])
            return dict(candidates=[draft(ep)],opportunities=[])
        review_count+=1
        if review_count==2:raise RuntimeError('quota reached')
        return response(review_inputs(prompt))
    with pytest.raises(RuntimeError):run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert len(run['shortlist'])==1 and scans==['ep1','ep2']
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert scans==['ep1','ep2','ep3'] and len(run['shortlist'])==3


@pytest.mark.parametrize('scope',[{}, {'selected_ids':[]}, {'selected_ids':['missing']}, {'selected_ids':['ep1']}])
def test_full_show_scope_always_uses_all_uploaded_episodes(scope):
    p=project(3)
    pl=plan_discovery(p,dict(scope,quantity='all'), 'gemini-3.5-flash-lite')
    assert pl['selected_ids']==['ep1','ep2','ep3']



def test_per_episode_hundred_outcomes_one_promo_each_and_ranked():
    p=project(100);pl=plan(p,quantity='all',per_episode=True,structure='either');run=new_run(pl)
    assert len(run['episode_results'])==100
    assert all(row['status']=='pending' for row in run['episode_results'])
    byid={ep['id']:ep for ep in p['episodes']};calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        if stage=='discovery-scan':
            source=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]
            return dict(candidates=[draft(byid[source['episode_id']]),draft(byid[source['episode_id']],start=30)],opportunities=[])
        assert stage=='discovery-review'  # Per-episode mode must never attempt cross-episode stitching.
        return response(review_inputs(prompt),score=4.2)
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert len(run['episode_results'])==len(run['shortlist'])==100
    assert calls==['discovery-scan']*100
    assert all(row['score'] is None for row in run['shortlist'])
    assert all(row['status']=='ready' and row.get('draft') for row in run['episode_results'])
    assert {row['episode_id'] for row in run['episode_results']}==set(byid)
    assert len({row['source_ids'][0] for row in run['shortlist']})==100
    assert all(len(row['source_ids'])==1 for row in run['shortlist'])


def test_per_episode_long_source_selects_best_cut_across_windows():
    p=project(count=100);run=new_run(plan(p,quantity='all',per_episode=True));ep=p['episodes'][0]
    later_start=run['plan']['windows'][1]['events'][0]['start']
    def model(pid,stage,prompt):
        if stage=='discovery-scan':
            source=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]
            start=source['events'][0]['start']
            return dict(candidates=[draft(ep,start=start)] if start in (0,later_start) else [],opportunities=[])
        clips=review_inputs(prompt);result=response(clips,score=4)
        for clip,row in zip(clips,result['results']):
            if clip['segments'][0]['start']>0:
                for r in row['rubric']:r['score']=4.8
        return result
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert len(run['candidates'])==2
    # Saved optional ratings may choose among actual candidates; generation itself
    # makes no automatic paid review request.
    for v in run['candidates']:
        rating=response([{'promo_id':v['id']}],score=4.8 if v['segments'][0]['start']>0 else 4)['results'][0]
        v.update(rating,review_status='reviewed')
    projected=project_episode_results(p,run);run.clear();run.update(projected)
    assert len(run['shortlist'])==len(run['episode_results'])==1
    assert run['shortlist'][0]['variations'][0]['segments'][0]['start']==later_start
    assert run['episode_results'][0]['draft']['segments'][0]['start']==later_start


def test_per_episode_preserves_no_qualifier_rows_and_rejected_draft_reasons():
    p=project(3);run=new_run(plan(p,quantity='all',per_episode=True));byid={ep['id']:ep for ep in p['episodes']}
    def model(pid,stage,prompt):
        if stage=='discovery-scan':
            eid=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]['episode_id']
            value=draft(byid[eid])
            if eid=='ep2':value['segments'][0]['start']=1
            return dict(candidates=[] if eid=='ep3' else [value],opportunities=[])
        clips=review_inputs(prompt)
        return response(clips,score=4,standalone=clips[0]['segments'][0]['episode_id']!='ep2')
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    rows={r['episode_id']:r for r in run['episode_results']}
    assert len(rows)==3 and len(run['shortlist'])==2
    assert rows['ep1']['status']=='ready'
    assert rows['ep2']['status']=='needs_repair' and rows['ep2']['draft'] and rows['ep2']['reasons']
    assert rows['ep3']['status']=='needs_repair' and rows['ep3']['reasons'] and 'draft' not in rows['ep3']


def test_per_episode_rejects_candidate_using_another_episode_and_retains_scoped_outcome():
    p=project(2);run=new_run(plan(p,quantity='all',per_episode=True))
    def model(pid,stage,prompt):
        assert stage=='discovery-scan'
        eid=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]['episode_id']
        return dict(candidates=[draft(p['episodes'][1])] if eid=='ep1' else [],opportunities=[])
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert len(run['shortlist'])==1 and not run['shortlist'][0]['eligible'] and len(run['episode_results'])==2
    row=run['episode_results'][0]
    assert row['episode_id']=='ep1' and row['status']=='needs_repair' and row['draft']
    assert any('outside' in reason.lower() or 'source selection' in reason.lower() for reason in row['reasons'])



def bad_episode_run(n=2):
    p=project(n);run=new_run(plan(p,quantity='all',per_episode=True));p['discovery_runs']=[run]
    byid={ep['id']:ep for ep in p['episodes']}
    def model(pid,stage,prompt):
        if stage=='discovery-scan':
            eid=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]['episode_id']
            value=draft(byid[eid]);value['segments'][0]['start']=1
            return dict(candidates=[value],opportunities=[])
        pytest.fail('Discovery generation must not automatically review a cut')
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    return p,run


def test_per_episode_failed_drafts_are_all_ranked_with_actual_ratings_and_checks():
    p,run=bad_episode_run(3)
    assert len(run['shortlist'])==3
    assert all(not row['eligible'] and row['score'] is None and row['status']=='needs_repair' for row in run['shortlist'])
    assert all(row['failed_checks'] and 'Needs repair' in row['posting_reason'] for row in run['shortlist'])
    assert all(row['draft'] and row['reasons'] for row in run['episode_results'])
    assert run['summary']['ready_episodes']==0 and run['summary']['needs_repair_episodes']==3
    before=copy.deepcopy(run)
    run['shortlist']=[]  # Mimic an older saved run that excluded its failed cuts.
    projected=project_episode_results(p,run)
    assert len(projected['shortlist'])==3 and run['shortlist']==[]
    assert projected['shortlist']==before['shortlist']


def test_per_episode_locally_valid_unrated_first_then_failed_and_optional_review_does_not_gate():
    p=project(3);run=new_run(plan(p,quantity='all',per_episode=True));calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        assert stage=='discovery-scan'
        eid=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]['episode_id']
        value=draft(next(ep for ep in p['episodes'] if ep['id']==eid))
        if eid=='ep3':value['segments'][0]['start']=1
        return dict(candidates=[value],opportunities=[])
    run_discovery(p,run,model,lambda p:None,lambda eps:eps)
    assert calls==['discovery-scan']*3
    assert [row['score'] for row in run['shortlist']]==[None,None,None]
    assert [row['eligible'] for row in run['shortlist']]==[True,True,False]
    review_episode(p,run,'ep1',lambda pid,stage,prompt:response(review_inputs(prompt),score=2,standalone=False),lambda p:None,lambda eps:eps)
    assert run['shortlist'][0]['source_ids']==['ep1'] and run['shortlist'][0]['score']==40
    assert run['shortlist'][0]['eligible'] and run['episode_results'][0]['status']=='ready'
    assert len(run['shortlist'][0]['variations'][0]['validation']['checks'])==5


def test_episode_repair_saves_proposal_without_swap_or_review_then_explicit_keep():
    p,run=bad_episode_run();before=copy.deepcopy(run['shortlist']);calls=[]
    def model(pid,stage,prompt):
        calls.append(stage)
        assert stage=='discovery-episode-repair'
        windows=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])
        assert {w['episode_id'] for w in windows}=={'ep1'}
        assert 'No dialogue cut mid-line' in prompt
        return dict(candidates=[dict(draft(p['episodes'][0],start=30),reason='Moved boundary',weakness='Different opening')],opportunities=[])
    repair_episode(p,run,'ep1',model,lambda p:None,lambda eps:eps)
    assert run['shortlist']==before and calls==['discovery-episode-repair']
    proposal=run['repair_proposals']['ep1']
    assert proposal['eligible'] and proposal['variation']['validation']['score'] is None
    assert proposal['change']=='Moved boundary' and proposal['tradeoff']=='Different opening'
    assert proposal['variation']['id'] not in {v['id'] for v in run['candidates']}
    decide_episode_repair(p,run,'ep1',True,lambda p:None)
    row=next(r for r in run['episode_results'] if r['episode_id']=='ep1')
    assert row['status']=='ready' and row['draft']['segments'][0]['start']==30 and row['score'] is None
    assert 'ep1' not in run['repair_proposals'] and run['repair_decisions'][-1]['keep_new']


def test_episode_repair_still_bad_proposal_can_be_chosen_with_warnings():
    p,run=bad_episode_run(1);before=copy.deepcopy(run['shortlist'])
    value=draft(p['episodes'][0],start=30);value['segments'][0]['start']=31
    repair_episode(p,run,'ep1',lambda *args:dict(candidates=[value],opportunities=[]),lambda p:None,lambda eps:eps)
    assert run['shortlist']==before and not run['repair_proposals']['ep1']['eligible']
    assert run['repair_proposals']['ep1']['variation']['validation']['checks']
    decide_episode_repair(p,run,'ep1',True,lambda p:None)
    assert 'ep1' not in run['repair_proposals']
    assert run['episode_results'][0]['draft']['segments'][0]['start']==31
    assert run['episode_results'][0]['status']=='needs_repair' and run['episode_results'][0]['score'] is None


def test_episode_repair_no_candidate_does_not_fabricate_and_network_failure_preserves_prior():
    p,run=bad_episode_run(1);prior=copy.deepcopy(run['shortlist'])
    repair_episode(p,run,'ep1',lambda *args:dict(candidates=[],opportunities=[]),lambda p:None,lambda eps:eps)
    assert run['shortlist']==prior and run['repair_proposals']['ep1']['status']=='no_candidate'
    assert run['repair_proposals']['ep1']['variation'] is None
    def interrupted(*args):raise RuntimeError('provider connection interrupted')
    with pytest.raises(RuntimeError):repair_episode(p,run,'ep1',interrupted,lambda p:None,lambda eps:eps)
    assert run['shortlist']==prior and run['repair_attempts'][-1]['status']=='error'


def test_episode_repair_foreign_source_proposal_is_flagged_without_review_or_swap():
    p,run=bad_episode_run(2);prior=copy.deepcopy(run['shortlist']);calls=[]
    def model(pid,stage,prompt):
        calls.append(stage);return dict(candidates=[draft(p['episodes'][1])],opportunities=[])
    repair_episode(p,run,'ep1',model,lambda p:None,lambda eps:eps)
    assert calls==['discovery-episode-repair'] and run['shortlist']==prior
    proposal=run['repair_proposals']['ep1']
    assert not proposal['eligible'] and proposal['variation']['scoped_episode_id']=='ep1'
    decide_episode_repair(p,run,'ep1',True,lambda p:None)
    chosen=next(row for row in run['episode_results'] if row['episode_id']=='ep1')
    assert not chosen['eligible'] and chosen['draft']['scoped_episode_id']=='ep1'


def test_episode_repair_empty_source_episode_remains_explicitly_without_draft():
    p=project();run=new_run(plan(p,quantity='all',per_episode=True))
    run_discovery(p,run,lambda *args:dict(candidates=[],opportunities=[]),lambda p:None,lambda eps:eps)
    repair_episode(p,run,'ep1',lambda *args:dict(candidates=[],opportunities=[]),lambda p:None,lambda eps:eps)
    assert not run['shortlist'] and 'draft' not in run['episode_results'][0]
    assert run['episode_results'][0]['last_repair']['status']=='no_candidate'


def test_projection_rebuilds_missing_validation_and_foreign_source_remains_failed_without_review():
    p,run=bad_episode_run(1)
    foreign=episode(2);p['episodes'].append(foreign)
    value=draft(foreign);value.update(id='foreign',scoped_episode_id='ep1',review_status='rejected')
    run['candidates']=[];run['rejected_candidates']=[value]
    projected=project_episode_results(p,run);v=projected['shortlist'][0]['variations'][0]
    assert not projected['shortlist'][0]['eligible'] and projected['shortlist'][0]['score'] is None
    assert all(k in v['validation'] for k in ('checks','evidence','warnings','duration'))
    assert not next(c for c in v['validation']['checks'] if c['name']=='Source selection')['passed']
    assert 'validation' not in value


def test_per_episode_prompt_requests_best_available_even_when_weak_without_invention():
    p=project();run=new_run(plan(p,quantity='all',per_episode=True));prompts=[]
    run_discovery(p,run,lambda pid,stage,prompt:(prompts.append(prompt) or dict(candidates=[],opportunities=[])),lambda p:None,lambda eps:eps)
    assert 'even when its story or ending is weak' in prompts[0]
    assert 'never fill a quota' not in prompts[0]
    assert 'return none if unsupported' not in prompts[0]
    assert 'never invent source dialogue or times' in prompts[0]


def test_run_view_projects_bad_saved_results_without_mutating_project():
    from discovery_api import run_view
    p,run=bad_episode_run(2);run['shortlist']=[]
    before=copy.deepcopy(p)
    view=run_view(p,run['id'])
    assert len(view['groups'])==2 and all(not row['eligible'] for row in view['groups'])
    assert p==before



def test_batch_repairs_are_sequential_episode_local_and_save_each_without_swapping():
    p,run=bad_episode_run(3);prior=copy.deepcopy(run['shortlist']);calls=[];saves=[]
    def model(pid,stage,prompt):
        assert stage=='discovery-episode-repair'
        source=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])
        eid=source[0]['episode_id'];calls.append(eid)
        assert all(w['episode_id']==eid for w in source)
        ep=next(ep for ep in p['episodes'] if ep['id']==eid)
        return dict(candidates=[draft(ep,start=30)],opportunities=[])
    repair_episodes(p,run,['ep2','ep1'],model,lambda p:saves.append(set(run.get('repair_proposals',{}))),lambda eps:eps)
    assert calls==['ep2','ep1'] and saves==[{'ep2'},{'ep2','ep1'}]
    assert run['shortlist']==prior and set(run['repair_proposals'])=={'ep1','ep2'}
    assert all(v['variation']['validation']['score'] is None for v in run['repair_proposals'].values())


def test_batch_validates_all_selections_before_any_paid_call_and_quota_preserves_completed():
    p,run=bad_episode_run(3);calls=[]
    # Reject malformed, duplicate or missing episode selections before a call.
    for selected in ([],['ep1','ep1'],['missing'],[True]):
        with pytest.raises(ValueError):repair_episodes(p,run,selected,lambda *args:calls.append(args),lambda p:None,lambda eps:eps)
    assert calls==[]
    def model(pid,stage,prompt):
        eid=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]['episode_id'];calls.append(eid)
        if eid=='ep2':raise RuntimeError('quota exhausted')
        ep=next(ep for ep in p['episodes'] if ep['id']==eid)
        return dict(candidates=[draft(ep,start=30)],opportunities=[])
    with pytest.raises(RuntimeError):repair_episodes(p,run,['ep1','ep2','ep3'],model,lambda p:None,lambda eps:eps)
    assert calls==['ep1','ep2'] and set(run['repair_proposals'])=={'ep1'}
    assert run['repair_attempts'][-1]['status']=='error'


def test_local_passed_cut_cannot_be_selected_for_repair_even_if_editorial_story_failed():
    p=project();run=new_run(plan(p,quantity='all',per_episode=True))
    run_discovery(p,run,lambda *args:dict(candidates=[draft(p['episodes'][0])],opportunities=[]),lambda p:None,lambda eps:eps)
    v=run['candidates'][0];v.update(response([{'promo_id':v['id']}],score=1,standalone=False)['results'][0],review_status='reviewed')
    with pytest.raises(ValueError,match='local checks'):validate_repair_selection(p,run,['ep1'])
    projected=project_episode_results(p,run)
    assert projected['episode_results'][0]['eligible'] and not projected['episode_results'][0]['repair_available']
    assert projected['shortlist'][0]['score']==20


@pytest.mark.parametrize('keep_new',[False,True])
def test_optional_review_of_failed_repair_choice_keeps_warnings(keep_new):
    p,run=bad_episode_run(1);calls=[]
    value=draft(p['episodes'][0],start=30);value['segments'][0]['start']=31
    repair_episode(p,run,'ep1',lambda *args:dict(candidates=[value],opportunities=[]),lambda p:None,lambda eps:eps)
    decide_episode_repair(p,run,'ep1',keep_new,lambda p:None)
    def optional(pid,stage,prompt):
        calls.append(stage)
        assert 'Local cut-check warnings' in prompt
        return response(review_inputs(prompt),score=3.6,standalone=False)
    review_episode(p,run,'ep1',optional,lambda p:None,lambda eps:eps)
    assert calls==['discovery-episode-review'] and run['shortlist'][0]['score']==72
    assert not run['shortlist'][0]['eligible']
    assert run['episode_results'][0]['draft']['segments'][0]['start']==(31 if keep_new else 1)


def test_discovery_api_batch_decision_and_optional_review_contract_without_network():
    from fastapi import FastAPI,HTTPException
    from discovery_api import install
    p,run=bad_episode_run(2);app=FastAPI();jobs=[]
    install(app,lambda pid:p,lambda p:None,lambda pid,op,body:(jobs.append((op,body)) or {'job_id':'j'}),
            {'model':run['plan']['model'],'key':'local-double','budget':10},lambda:0,lambda:False)
    routes={r.path:r.endpoint for r in app.routes if hasattr(r,'endpoint')}
    base='/api/projects/{pid}/discovery/{rid}'
    result=routes[base+'/repairs']('p',run['id'],{'episode_ids':['ep2','ep1']})
    assert result['job_id']=='j' and jobs[-1]==('discovery_episode_repairs',{'run_id':run['id'],'episode_ids':['ep2','ep1']})
    routes[base+'/episodes/{episode_id}/virality-review']('p',run['id'],'ep1')
    assert jobs[-1][0]=='discovery_episode_review'
    repair_episode(p,run,'ep1',lambda *args:dict(candidates=[draft(p['episodes'][0],start=30)],opportunities=[]),lambda p:None,lambda eps:eps)
    result=routes[base+'/episodes/{episode_id}/repair-decision']('p',run['id'],'ep1',{'keep_new':True})
    assert result['discovery_report']['episode_results'][0]['eligible']
    routes[base+'/episodes/{episode_id}/virality-review']('p',run['id'],'ep1')
    assert jobs[-1]==('discovery_episode_review',{'run_id':run['id'],'episode_id':'ep1'})


def test_reduced_discovery_checks_do_not_gate_on_angle_question_or_story_evidence():
    p=project();run=new_run(plan(p,quantity='all',per_episode=True));value=draft(p['episodes'][0])
    value.pop('angle');value.pop('question');value['beats']=[value['beats'][0]]
    run_discovery(p,run,lambda *args:dict(candidates=[value],opportunities=[]),lambda p:None,lambda eps:eps)
    row=run['episode_results'][0]
    assert row['eligible'] and row['score'] is None
    assert [c['name'] for c in row['draft']['validation']['checks']]==[
        'Source selection','Cut structure','20–60 second duration','No dialogue cut mid-line','Hook in first 3 seconds']



def test_keep_original_pins_exact_cut_after_clearing_rating_and_preserves_proposal_history():
    p,run=bad_episode_run(1)
    first=run['rejected_candidates'][0]
    second=copy.deepcopy(first);second['id']='another-failed-draft';second['segments'][0]['start']=2
    for v,score in ((first,4.8),(second,4)):
        v.update(response([{'promo_id':v['id']}],score=score)['results'][0],review_status='reviewed')
    run['rejected_candidates'].append(second)
    projected=project_episode_results(p,run);run.clear();run.update(projected)
    original_id=run['shortlist'][0]['id']
    model=lambda *args:dict(candidates=[draft(p['episodes'][0],start=30)],opportunities=[])
    repair_episode(p,run,'ep1',model,lambda p:None,lambda eps:eps)
    proposal_id=run['repair_proposals']['ep1']['id']
    repair_episode(p,run,'ep1',model,lambda p:None,lambda eps:eps)
    assert run['repair_proposal_history'][-1]['id']==proposal_id
    decide_episode_repair(p,run,'ep1',False,lambda p:None)
    assert run['shortlist'][0]['id']==original_id and run['shortlist'][0]['score'] is None
