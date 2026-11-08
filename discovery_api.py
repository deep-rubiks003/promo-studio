"""Additive show-discovery routes; manual drafts are never replaced."""
import copy
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from models import model_rates
from discovery import (plan_discovery,new_run,project_episode_results,validate_repair_selection,
                       decide_episode_repair)

def find_run(p,rid):
    run=next((r for r in p.get('discovery_runs',[]) if r['id']==rid),None)
    if not run:raise HTTPException(404,'Discovery run not found.')
    return run

def run_view(p,rid):
    view=copy.deepcopy(p);run=find_run(view,rid)
    if run['plan'].get('per_episode'):
        projected=project_episode_results(view,run)
        run.clear();run.update(projected)
    plan=run['plan']
    view.update(groups=copy.deepcopy(run.get('shortlist',[])),brief=dict(plan,structure=plan['structure'],promo_mode='discovery'),proposals={},discovery_view=rid,discovery_report=run)
    view.pop('pending_generation',None)
    view.pop('spoiler_batch_check',None)
    if run.get('spoiler_batch_check'):view['spoiler_batch_check']=run['spoiler_batch_check']
    return view

def install(app,load,save,start,state,spent,is_active):
    def preview(p,body):
        try:plan=plan_discovery(p,body,state['model'])
        except (ValueError,TypeError,KeyError) as e:raise HTTPException(400,str(e))
        ir,orr=model_rates(state['model']);maximum=plan['estimated_calls']['maximum']
        # Conservative attempted-request upper bound, including a possible retry.
        upper=2*((plan.get('estimated_max_input_bytes',maximum*100000)+maximum*3000)*ir+maximum*4096*orr)/1_000_000
        summary={k:v for k,v in plan.items() if k not in ('windows','batches')}
        summary.update(batch_count=len(plan['batches']),window_count=len(plan['windows']),cost_upper_usd=round(upper,4),budget_remaining_usd=round(max(0,state['budget']-spent()),4),within_budget=upper<=state['budget']-spent(),note='Estimate includes worst-case retries and output allowance; actual usage varies. Spending guard can pause a large run before completion. Optional virality review and spoiler checks are additional.')
        return plan,summary

    @app.post('/api/projects/{pid}/discovery/preview')
    def discovery_preview(pid:str,body:dict):return preview(load(pid),body)[1]

    @app.post('/api/projects/{pid}/discovery')
    def discover(pid:str,body:dict):
        if not state['key']:raise HTTPException(400,'Connect your chosen model before starting discovery. Preview uses no AI.')
        if is_active():raise HTTPException(409,'Wait for the current job or stop it first.')
        p=load(pid);plan,_=preview(p,body);run=new_run(plan)
        p.setdefault('discovery_runs',[]).append(run);save(p)
        result=start(pid,'discovery',{'run_id':run['id']});return dict(result,run_id=run['id'])

    @app.post('/api/projects/{pid}/discovery/{rid}/resume')
    def resume_discovery(pid:str,rid:str):
        p=load(pid);run=find_run(p,rid)
        if run['plan']['model']!=state['model']:raise HTTPException(400,'Select the model used by this run to resume its saved checkpoints.')
        return dict(start(pid,'resume_discovery',{'run_id':rid}),run_id=rid)

    @app.get('/api/projects/{pid}/discovery/{rid}/view')
    def view_discovery(pid:str,rid:str):return run_view(load(pid),rid)

    @app.post('/api/projects/{pid}/discovery/{rid}/episodes/{episode_id}/repair')
    def repair_discovery_episode(pid:str,rid:str,episode_id:str,body:dict=None):
        if is_active():raise HTTPException(409,'Wait for the current job or stop it first.')
        if not state['key']:raise HTTPException(400,'Connect your chosen model before repairing an episode.')
        p=load(pid);run=find_run(p,rid)
        if not run['plan'].get('per_episode'):raise HTTPException(400,'Choose a per-episode discovery run.')
        if episode_id not in run['plan']['selected_ids']:raise HTTPException(404,'Episode not found in this discovery run.')
        if run['plan']['model']!=state['model']:raise HTTPException(400,'Select the model used by this run to repair its saved episode.')
        try:validate_repair_selection(p,run,[episode_id])
        except ValueError as exc:raise HTTPException(400,str(exc))
        return dict(start(pid,'discovery_episode_repair',{'run_id':rid,'episode_id':episode_id}),run_id=rid,episode_id=episode_id)

    @app.post('/api/projects/{pid}/discovery/{rid}/repairs')
    def repair_discovery_episodes(pid:str,rid:str,body:dict):
        if is_active():raise HTTPException(409,'Wait for the current job or stop it first.')
        if not state['key']:raise HTTPException(400,'Connect your chosen model before requesting repairs.')
        p=load(pid);run=find_run(p,rid);ids=body.get('episode_ids')
        if run['plan']['model']!=state['model']:raise HTTPException(400,'Select the model used by this run.')
        try:validate_repair_selection(p,run,ids)
        except ValueError as exc:raise HTTPException(400,str(exc))
        return dict(start(pid,'discovery_episode_repairs',{'run_id':rid,'episode_ids':ids}),run_id=rid,episode_ids=ids)

    @app.post('/api/projects/{pid}/discovery/{rid}/episodes/{episode_id}/repair-decision')
    def decide_discovery_repair(pid:str,rid:str,episode_id:str,body:dict):
        if is_active():raise HTTPException(409,'Wait for the current job or stop it first.')
        p=load(pid);run=find_run(p,rid)
        try:decide_episode_repair(p,run,episode_id,body.get('keep_new'),save)
        except ValueError as exc:raise HTTPException(400,str(exc))
        return run_view(p,rid)

    @app.post('/api/projects/{pid}/discovery/{rid}/episodes/{episode_id}/virality-review')
    def review_discovery_episode(pid:str,rid:str,episode_id:str):
        if is_active():raise HTTPException(409,'Wait for the current job or stop it first.')
        if not state['key']:raise HTTPException(400,'Connect your chosen model before requesting a virality review.')
        p=load(pid);run=project_episode_results(p,find_run(p,rid))
        if run['plan']['model']!=state['model']:raise HTTPException(400,'Select the model used by this run.')
        row=next((r for r in run.get('episode_results',[]) if r['episode_id']==episode_id),None)
        if not row:raise HTTPException(404,'Episode not found in this discovery run.')
        if not row.get('draft'):raise HTTPException(400,'There is no saved draft to review for this episode.')
        return dict(start(pid,'discovery_episode_review',{'run_id':rid,'episode_id':episode_id}),run_id=rid,episode_id=episode_id)

    @app.post('/api/projects/{pid}/discovery/{rid}/spoiler-check')
    def check_discovery(pid:str,rid:str):
        run=find_run(load(pid),rid)
        if run.get('status')!='done' or not run.get('shortlist'):raise HTTPException(400,'Complete a discovery shortlist first.')
        return dict(start(pid,'discovery_spoilers',{'run_id':rid}),run_id=rid)

    @app.get('/api/projects/{pid}/discovery/{rid}/export')
    def export_discovery(pid:str,rid:str):
        p=load(pid);run=find_run(p,rid);ids=set(run['plan']['selected_ids'])
        return JSONResponse(dict(project=p['name'],discovery=run,episodes=[e for e in p['episodes'] if e['id'] in ids],methodology='Coverage-first discovery, deterministic source validation, clip-only batch editorial review. Scores are editorial estimates, not viral probability. No embeddings or vector search.'),headers={'Content-Disposition':'attachment; filename="show-discovery.json"'})
