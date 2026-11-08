import copy
import math
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid
import random
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from parser import parse_episode, validate_cut
from provider import Gemini, read_json, ProviderError, MAX_OUTPUT_TOKENS
from models import MODELS, DEFAULT_MODEL, model_rates
from discovery import run_discovery
from discovery_api import install as install_discovery, find_run, run_view
from workflow import build_brief, generate_promos, locate, check_spoilers, check_all_spoilers, DRAFT, check_only, clear_review, repair_selection, manual_bulk_repair
from handoff import sheet
from saved_output import view as saved_output_view
from job_runtime import JobStore, DurableJob, Runtime, JobCancelled, TERMINAL, isolated_generate

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('PROMO_STUDIO_DATA',str(ROOT/'data')))
DATA.mkdir(parents=True,exist_ok=True)
DB=DATA/'studio.sqlite3'
def db():
    c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row;return c
with db() as c:
    c.executescript('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,body TEXT); CREATE TABLE IF NOT EXISTS raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT); CREATE TABLE IF NOT EXISTS usage(keyhash TEXT PRIMARY KEY,spent REAL); CREATE TABLE IF NOT EXISTS api_errors(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);')
TOKEN=secrets.token_urlsafe(32)
state={'key':'','model':DEFAULT_MODEL,'budget':2.0,'keyhash':''}
job_store=JobStore(db)
job_store.initialize()
runtimes={}
jobs={};executor=ThreadPoolExecutor(max_workers=1);gate=threading.Lock();active=set();context=threading.local()
@asynccontextmanager
async def lifespan(app):
    recover_jobs()
    try:yield
    finally:stop_local_workers()

app=FastAPI(docs_url=None,redoc_url=None,lifespan=lifespan)

def recover_jobs():
    # Lifecycle hooks do not run when Windows spawn imports server.py as __mp_main__.
    job_store.initialize(recover=True)

def stop_local_workers():
    with gate:running=[runtimes[jid] for jid in active if jid in runtimes]
    for runtime in running:
        runtime.interrupted=True
        runtime.cancel()
    for runtime in running:runtime.finished.wait(3)

@app.middleware('http')
async def local_only(request,call_next):
    host=request.headers.get('host','').split(':')[0]
    if host not in ('127.0.0.1','localhost','testserver'): return JSONResponse({'detail':'Local connections only.'},403)
    origin=request.headers.get('origin')
    if origin and origin not in ('http://'+request.headers.get('host',''),): return JSONResponse({'detail':'Use this app from its local address.'},403)
    if request.method!='GET' and request.headers.get('x-studio-token')!=TOKEN: return JSONResponse({'detail':'Reload the studio to reconnect this session.'},403)
    if int(request.headers.get('content-length','0'))>8_000_000: return JSONResponse({'detail':'Upload limit is 8 MB per project.'},413)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    return response

def load(pid):
    with db() as c:r=c.execute('SELECT body FROM projects WHERE id=?',(pid,)).fetchone()
    if not r: raise HTTPException(404,'Project not found.')
    return json.loads(r[0])
def save(p):
    runtime=getattr(context,'runtime',None)
    if runtime:
        with runtime.lock:
            runtime.check()
            return _save(p)
    return _save(p)

def _save(p):
    p['updated_at']=time.time()
    with db() as c:c.execute('INSERT OR REPLACE INTO projects VALUES (?,?)',(p['id'],json.dumps(p,ensure_ascii=False)))
    return p
def spent():
    with db() as c:r=c.execute('SELECT spent FROM usage WHERE keyhash=?',(state['keyhash'],)).fetchone()
    return r[0] if r else 0
def status():
    with db() as c: projects=[json.loads(r[0]) for r in c.execute('SELECT body FROM projects')]
    stored=job_store.all();latest={}
    for j in stored:latest.setdefault(j['project_id'],j)
    return dict(active_jobs=[j for j in stored if j['status'] not in TERMINAL],latest_jobs=list(latest.values()),configured=bool(state['key']),model=state['model'],budget=state['budget'],spent=round(spent(),4),token=TOKEN,projects=[{k:p[k] for k in ('id','name','updated_at')} for p in sorted(projects,key=lambda p:p['updated_at'],reverse=True)])

@app.get('/api/status')
def get_status():return status()
@app.post('/api/connect')
def connect(body:dict):
    with gate:
        if active: raise HTTPException(409,'Wait for the current job before changing the connection.')
        key=str(body.get('key','')).strip();model=str(body.get('model',DEFAULT_MODEL)).strip()
        try:
            model_rates(model)
            budget=float(body.get('budget',2))
            if isinstance(body.get('budget'),bool) or not math.isfinite(budget) or budget<=0 or not key: raise ValueError('Enter a key and a positive, finite spending limit in USD.')
            Gemini(key,model).validate()
        except (ValueError,TypeError,OverflowError) as e:raise HTTPException(400,str(e))
        state.update(key=key,model=model,budget=budget,keyhash=hashlib.sha256(key.encode()).hexdigest())
    return status()
@app.post('/api/projects')
def create(body:dict):
    files=body.get('files',[])
    if not isinstance(files,list) or not 1<=len(files)<=100:raise HTTPException(400,'Choose 1–100 timestamped TXT files.')
    try:
        episodes=[parse_episode(str(f['name']),str(f['text'])) for f in files]
        if len({e['id'] for e in episodes})!=len(episodes):raise ValueError('Duplicate files found. Import each episode once.')
        episodes.sort(key=lambda e:(e['number'],e['filename']))
    except (KeyError,TypeError,ValueError) as e:raise HTTPException(400,str(e))
    return save(dict(id=uuid.uuid4().hex,name=str(body.get('name','Untitled show'))[:120],episodes=episodes,analysis=None,brief=None,groups=[],proposals={},updated_at=time.time()))
@app.get('/api/projects/{pid}')
def get_project(pid:str):
    p=load(pid)
    if isinstance(p.get('brief'),dict) and {'selected_ids','minimum','maximum','structure'}<=set(p['brief']):
        for group in p.get('groups',[]):
            b=dict(p['brief'],selected_ids=group.get('source_ids',p['brief']['selected_ids']))
            for v in group.get('variations',[]):
                check_only(p,v,b,clear=False)
        for vid,proposal in p.get('proposals',{}).items():
            if not isinstance(proposal,dict) or not isinstance(proposal.get('variation'),dict):continue
            try:
                group,_=locate(p,vid)
                b=dict(p['brief'],selected_ids=group.get('source_ids',p['brief']['selected_ids']))
                check_only(p,proposal['variation'],b,clear=False)
            except (ValueError,KeyError,TypeError):continue
    stored=job_store.all()
    from discovery import project_episode_results
    p['discovery_runs']=[project_episode_results(p,r) if r.get('plan',{}).get('per_episode') else r for r in p.get('discovery_runs',[])]
    for run in p.get('discovery_runs',[]):
        latest=next((j for j in stored if j.get('project_id')==pid and j.get('run_id')==run['id'] and j.get('operation') in ('discovery','resume_discovery')),None)
        if run.get('status')!='done' and latest and latest['status'] in ('error','cancelled','interrupted'):
            run['status']=latest['status'];run['last_job_message']=latest.get('error') or latest.get('message')
    return p

@app.get('/api/projects/{pid}/saved-output')
def get_saved_output(pid:str):return saved_output_view(load(pid))

def call(pid,stage,prompt,output_limit=None,retry_limit=1,minimal=False,raw_only=False):
    if stage.startswith('discovery-') and output_limit is None:output_limit=4096
    runtime=getattr(context,'runtime',None)
    job=getattr(context,'job',None)
    if runtime:runtime.check()
    if job:
        stages=copy.deepcopy(job.get('stages',[]));stages.append(dict(name=stage,status='running',started_at=time.time()))
        job.update(stage=stage,stages=stages)
    limit=MAX_OUTPUT_TOKENS if output_limit is None else output_limit
    input_rate,output_rate=model_rates(state['model'])
    reserve=((len(prompt.encode())+3000)*input_rate+limit*output_rate)/1_000_000
    for attempt in range(retry_limit+1):
        if runtime:runtime.check()
        with db() as c:
            current=spent()
            if current+reserve>state['budget']:raise ValueError(f'This request would exceed your ${state["budget"]:,.2f} spending limit, including held reservations. Completed work is saved. Change the spending limit in connection settings to continue.')
            c.execute('INSERT OR REPLACE INTO usage VALUES (?,?)',(state['keyhash'],current+reserve))
        try:
            options={}
            if output_limit is not None:options['max_output_tokens']=output_limit
            if minimal:options['minimal']=True
            raw=isolated_generate(state['key'],state['model'],prompt,options,runtime) if runtime else Gemini(state['key'],state['model']).generate(prompt,**options)
        except ProviderError as e:
            record=dict(e.diagnostic(),model=state['model'],at=time.time(),attempt=attempt+1,prompt_bytes=len(prompt.encode()),reserved_usd=reserve)
            with db() as c:
                c.execute('CREATE TABLE IF NOT EXISTS api_errors(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT)')
                c.execute('INSERT INTO api_errors VALUES (?,?,?,?)',(uuid.uuid4().hex,pid,stage,json.dumps(record)))
            if e.retryable and attempt<retry_limit and (e.retry_after is None or e.retry_after<=30):
                delay=max(e.retry_after or 0,2**(attempt+1)+random.uniform(0,.5))
                job=getattr(context,'job',None)
                if job:job['message']=f'Gemini temporarily unavailable (HTTP {e.http_status}); retrying in {delay:.0f}s'
                if runtime:
                    if runtime.cancelled.wait(delay):runtime.check()
                else:time.sleep(delay)
                continue
            raise
        usage=raw.get('usageMetadata',{})
        actual=(usage.get('promptTokenCount',0)*input_rate+(usage.get('candidatesTokenCount',0)+usage.get('thoughtsTokenCount',0))*output_rate)/1_000_000
        if usage:
            with db() as c:c.execute('UPDATE usage SET spent=spent-?+? WHERE keyhash=?',(reserve,actual,state['keyhash']))
        with db() as c:c.execute('INSERT INTO raw VALUES (?,?,?,?)',(uuid.uuid4().hex,pid,stage,json.dumps(dict(model=state['model'],received_at=time.time(),brief=getattr(context,'brief',None),response=raw),ensure_ascii=False)))
        if job:
            stages=copy.deepcopy(job['stages']);stages[-1].update(status='done',finished_at=time.time());job['stages']=stages
        if runtime:runtime.check()
        return raw if raw_only else read_json(raw)

@app.get('/api/diagnostics')
def diagnostics():
    with db() as c:
        try:errors=[dict(project_id=r['project_id'],stage=r['stage'],**json.loads(r['body'])) for r in c.execute('SELECT project_id,stage,body FROM api_errors ORDER BY rowid DESC LIMIT 10')]
        except sqlite3.OperationalError:errors=[]
    return dict(model=state['model'],configured=bool(state['key']),budget_held=round(spent(),4),budget=state['budget'],recent_errors=errors,jobs=job_store.all()[:10])

@app.post('/api/diagnostics/probe')
def probe(body:dict):
    if not state['key']:raise HTTPException(400,'Connect your Gemini key first.')
    with gate:
        if active:raise HTTPException(409,'Wait for the current generation before running diagnostics.')
        marker=uuid.uuid4().hex;active.add(marker)
    try:
        context.brief=None;context.job=None;context.runtime=None
        minimal=body.get('minimal') is True
        raw=call('__diagnostics__','minimal-probe' if minimal else 'production-config-probe','Return a JSON object with one field: ok set to true.',output_limit=256,retry_limit=0,minimal=minimal,raw_only=True)
        return dict(http_status=200,model=state['model'],configuration='minimal' if minimal else 'production',finish_reason=raw.get('candidates',[{}])[0].get('finishReason'),usage=raw.get('usageMetadata',{}))
    except ValueError as e:raise HTTPException(502,str(e))
    finally:
        with gate:active.discard(marker)


def material(episodes):
    return [{'episode_id':e['id'],'id':e['id'],'episode':e['number'],'logline':e['logline'],'duration':e['duration'],'events':[x for x in e['events'] if x['kind']!='camera']} for e in episodes]

VARIANT=DRAFT

def review(p,v,brief,allow_failed=False):
    from workflow import normalize_sources
    normalize_sources(v,p['episodes'],brief['selected_ids'])
    preflight=validate_cut(v,p['episodes'],brief['selected_ids'],brief['minimum'],brief['maximum'],brief['structure'])
    failed=[c for c in preflight['checks'] if not c['passed']]
    if failed and not allow_failed:
        v.update(narrative={},rubric=[],review_status='needs_repair',review_notes='Local source checks failed; no AI review was requested. '+ '; '.join(c['name']+': '+c['detail'] for c in failed),validation=preflight)
        return v
    clip=[]
    if not isinstance(v.get('segments'),list):raise ValueError('Malformed source segments returned by Gemini.')
    for s in v['segments']:
        if not isinstance(s,dict):raise ValueError('Malformed source segment returned by Gemini.')
        ep=next((e for e in p['episodes'] if e['id']==s.get('episode_id')),None)
        if ep and isinstance(s.get('start'),(int,float)) and isinstance(s.get('end'),(int,float)):
            clip.append({'segment':s,'events':[e for e in ep['events'] if e['start']>=s['start'] and e['end']<=s['end']]})
    result=call(p['id'],'independent-clip-review','Review ONLY the clip below as a newcomer, with no show context. Assess whether it has standalone clarity, escalation, an unresolved cliffhanger, and coherent start-to-end story. Be strict: do not assume unseen context. Return JSON {narrative:{standalone:boolean,escalation:boolean,cliffhanger:boolean,coherent:boolean},review_notes:string,rubric:[{criterion,score,reason}]}. Exactly five criteria: Hook, Standalone clarity, Escalation, Cliffhanger, Cut quality. Scores 0–5. Hook must earn attention first3sec. Stitched cuts need understandable transitions. Local cut-check warnings (do not invent missing source or assume these passed): '+json.dumps(failed,ensure_ascii=False)+' Clip: '+json.dumps(clip,ensure_ascii=False))
    from discovery import _review_result
    rid=v.get('id','clip')
    assessment=_review_result(dict(results=[dict(result,promo_id=rid)]),[rid])[rid]
    v.update(assessment);v['review_status']='reviewed'
    v['validation']=validate_cut(v,p['episodes'],brief['selected_ids'],brief['minimum'],brief['maximum'],brief['structure'])
    return v

def new_job(pid,operation,body,message='Waiting to start'):
    jid=uuid.uuid4().hex
    runtime=Runtime();runtimes[jid]=runtime
    jobs[jid]=DurableJob(job_store,id=jid,project_id=pid,operation=operation,body=copy.deepcopy(body),status='queued',message=message,stages=[],run_id=body.get('run_id'),created_at=time.time(),updated_at=time.time())
    active.add(jid)
    return jid

def finish_job(job,status,message,**extra):
    stages=copy.deepcopy(job.get('stages',[]))
    for stage in stages:
        if stage['status']=='running':stage.update(status=status,finished_at=time.time())
    job.update(status=status,message=message,stages=stages,finished_at=time.time(),**extra)

def release_job(jid):
    with gate:active.discard(jid)
    runtimes[jid].finished.set()
    context.job=None;context.runtime=None;context.brief=None

def start(pid,operation,body):
    load(pid)
    if not state['key']:raise HTTPException(400,'Connect your Gemini key first.')
    with gate:
        if active:raise HTTPException(409,'Another job is running. Wait for it to finish.')
        jid=new_job(pid,operation,body)
    def run():
        job=jobs[jid];context.job=job;context.runtime=runtimes[jid]
        try:
            with context.runtime.lock:
                context.runtime.check();job.update(status='running',message='Reading source material',started_at=time.time())
            p=load(pid)
            context.brief=body if operation=='generate' else p.get('brief')
            if operation in ('discovery','resume_discovery','discovery_spoilers','discovery_episode_repair','discovery_episode_repairs','discovery_episode_review'):
                run=find_run(p,body['run_id']);context.brief=run['plan']
                job.update(run_id=run['id'])
                if operation=='discovery_episode_repairs':
                    from discovery import repair_episodes
                    job['message']='Proposing repairs for selected episodes'
                    repair_episodes(p,run,body['episode_ids'],call,save,material)
                elif operation=='discovery_episode_review':
                    from discovery import review_episode
                    job['message']='Running optional Virality Review'
                    review_episode(p,run,body['episode_id'],call,save,material)
                elif operation=='discovery_episode_repair':
                    from discovery import repair_episode
                    job['message']='Repairing only the selected episode promo'
                    repair_episode(p,run,body['episode_id'],call,save,material)
                elif operation=='discovery_spoilers':
                    view=run_view(p,run['id']);check_all_spoilers(view,call,material)
                    run['shortlist']=view['groups'];run['spoiler_batch_check']=view['spoiler_batch_check']
                else:
                    run_discovery(p,run,call,save,material)
            elif operation=='manual_bulk_repair':
                manual_bulk_repair(p,body['variation_ids'],job,call,save,material)
            elif operation=='virality-review':
                group,v=locate(p,body['variation_id'])
                brief=dict(p['brief'],selected_ids=group.get('source_ids',p['brief']['selected_ids']))
                job['message']='Running optional Virality Review'
                review(p,v,brief,allow_failed=True)
            elif operation=='analyze':
                chosen=[e for e in p['episodes'] if e['id'] in body['selected_ids']]
                result=call(pid,'selected-episode-analysis','Read ONLY the chosen episodes and infer genres with source evidence, central conflict, relationships and tropes. This is a limited selection, not the full show; acknowledge uncertainty. Return JSON {title,logline,primary_genre,secondary_genre,themes:[string],relationships:[string],evidence:[string],warnings:[string]}. Evidence strings must name episode/time. Flag identity ambiguities instead of merging characters. Source: '+json.dumps(material(chosen),ensure_ascii=False))
                for k in ('title','logline','primary_genre','secondary_genre'):
                    if not isinstance(result.get(k),str):raise ValueError('Incomplete show analysis. Retry; current project is preserved.')
                p['analysis']=result;p['analysis_ids']=body['selected_ids']
            elif operation in ('generate','resume_generation'):
                context.brief=body
                generate_promos(p,body,job,call,check_only,save,material,resume=operation=='resume_generation')
            elif operation=='spoiler-check':
                job['message']='Checking all final promos together in one spoiler assessment'
                check_all_spoilers(p,call,material)
            else:
                group,old=locate(p,body.get('variation_id'))
                brief=dict(p['brief'],selected_ids=group.get('source_ids',p['brief']['selected_ids']))
                selected=[e for e in p['episodes'] if e['id'] in brief['selected_ids']]
                from workflow import rejected_checks, normalize_refinement, REFINEMENT_FORMAT
                feedback=' Repair these failures: '+json.dumps(rejected_checks(old)) if not old.get('validation',{}).get('eligible') else ''
                result=call(pid,'refinement-proposal','Propose an existing-source recut. Return ONE JSON object with exactly these outer keys: variation, change, tradeoff. variation MUST be a nested OBJECT, never a title string. Put all draft fields INSIDE variation, not beside it. Shape (illustrative times only; use real source bounds and brief): '+json.dumps(REFINEMENT_FORMAT)+' '+VARIANT+' Preserve untargeted parts where possible. If impossible explain weakness, do not invent. Target:'+str(body.get('part'))+' Request:'+str(body.get('prompt'))+feedback+' Current:'+json.dumps(old)+' Brief:'+json.dumps(brief)+' Source:'+json.dumps(material(selected),ensure_ascii=False))
                result=normalize_refinement(result)
                v=result['variation']
                v['id']=old['id'];v.pop('spoiler_check',None);v.pop('review_status',None);job['message']='Checking proposed source cut';check_only(p,v,brief)
                p['proposals'][old['id']]=result
            with context.runtime.lock:
                save(p);context.runtime.check();finish_job(job,'done',('Batch complete — '+str(p['generation_summary']['passed'])+' passed; '+str(p['generation_summary']['needs_repair'])+' need repair') if job.get('operation') in ('generate','resume_generation') and p.get('generation_summary') else 'Ready')
        except JobCancelled as e:
            if operation=='manual_bulk_repair' and 'p' in locals() and p.get('manual_repair_batch'):
                batch=p['manual_repair_batch'];batch['status']='paused'
                for item in batch['items']:
                    if item['status']=='running':item.update(status='paused',error=str(e))
                with context.runtime.lock:_save(p)
            finish_job(job,'interrupted' if context.runtime.interrupted else 'cancelled',str(e))
        except Exception as e:
            if context.runtime.cancelled.is_set():finish_job(job,'cancelled','Stopped by you. Completed saved outputs are preserved.')
            else:finish_job(job,'error','Operation stopped; existing results preserved',error=str(e) if isinstance(e,(ValueError,HTTPException)) else 'Unexpected response format. Try fewer episodes or another brief.')
        finally:
            release_job(jid)
    executor.submit(run);return {'job_id':jid}

@app.post('/api/projects/{pid}/resume')
def resume(pid:str):
    p=load(pid)
    if p.get('pending_generation'):
        return start(pid,'resume_generation',p['pending_generation']['brief'])
    if not state['key']:raise HTTPException(400,'Connect your Gemini key first.')
    if active:raise HTTPException(409,'Wait for the current job.')
    with db() as c:records=[(r['stage'],json.loads(r['body'])) for r in c.execute('SELECT stage,body FROM raw WHERE project_id=? ORDER BY rowid',(pid,))]
    candidates=[r for stage,r in records if stage=='candidate-generation']
    if not candidates:raise HTTPException(400,'No saved generation to resume. Create promos first.')
    raw=candidates[-1];result=read_json(raw['response']);brief=raw.get('brief')
    if not brief:raise HTTPException(400,'No saved trial brief. Set your brief and generate again.')
    if set(brief['selected_ids'])!=set(p.get('analysis_ids',[])):raise HTTPException(400,'The saved generation uses a different selection. Analyze that exact selection before resuming.')
    with gate:
        if active:raise HTTPException(409,'Wait for the current job.')
        jid=new_job(pid,'resume_saved',brief,'Resuming saved source cuts')
    def run():
        job=jobs[jid];context.job=job;context.runtime=runtimes[jid]
        try:
            with context.runtime.lock:
                context.runtime.check();job.update(status='running',started_at=time.time())
            context.brief=brief
            groups=result.get('groups',[])[:brief['group_count']]
            saved_reviews=[read_json(r['response']) for stage,r in records if stage=='independent-clip-review' and r['received_at']>raw['received_at']]
            n=0
            for g in groups:
                g['id']=uuid.uuid4().hex
                g['variations']=g['variations'][:3];fingerprints=set()
                for v in g['variations']:
                    v['id']=uuid.uuid4().hex;job['message']=f'Resuming review {n+1}'
                    if n<len(saved_reviews):
                        v.update(saved_reviews[n]);v['validation']=validate_cut(v,p['episodes'],brief['selected_ids'],brief['minimum'],brief['maximum'],brief['structure'])
                    else:check_only(p,v,brief)
                    fingerprint=json.dumps(v.get('segments'),sort_keys=True)
                    fingerprints.add(fingerprint)
                    n+=1
            if not any(v['validation']['eligible'] for g in groups for v in g['variations']):raise ValueError('No eligible cuts. Existing results preserved.')
            with context.runtime.lock:
                p.update(groups=groups,brief=brief,proposals={},failures=result.get('failures',[]));save(p);context.runtime.check();finish_job(job,'done',('Batch complete — '+str(p['generation_summary']['passed'])+' passed; '+str(p['generation_summary']['needs_repair'])+' need repair') if job.get('operation') in ('generate','resume_generation') and p.get('generation_summary') else 'Ready')
        except JobCancelled as e:finish_job(job,'interrupted' if context.runtime.interrupted else 'cancelled',str(e))
        except Exception as e:
            finish_job(job,'cancelled' if context.runtime.cancelled.is_set() else 'error','Resume stopped; saved responses preserved',error=str(e))
        finally:release_job(jid)
    executor.submit(run);return {'job_id':jid}

@app.post('/api/projects/{pid}/analyze')
def analyze(pid:str,body:dict):
    p=load(pid);ids=body.get('selected_ids',[])
    if not isinstance(ids,list) or not ids or not set(ids)<=set(e['id'] for e in p['episodes']):raise HTTPException(400,'Choose episodes for analysis first. No scripts were sent.')
    return start(pid,'analyze',{'selected_ids':ids})
@app.post('/api/projects/{pid}/generate')
def generate(pid:str,body:dict):
    try:brief=build_brief(load(pid),body)
    except (ValueError,TypeError,KeyError) as e:raise HTTPException(400,str(e))
    return start(pid,'generate',brief)

@app.post('/api/projects/{pid}/repairs')
def manual_repairs(pid:str,body:dict):
    p=load(pid)
    if p.get('pending_generation'):raise HTTPException(400,'Resolve the pending generation before repairing final drafts.')
    try:repair_selection(p,body.get('variation_ids'))
    except (ValueError,TypeError,KeyError) as e:raise HTTPException(400,str(e))
    return start(pid,'manual_bulk_repair',{'variation_ids':body['variation_ids']})

@app.post('/api/projects/{pid}/virality-review')
def virality_review(pid:str,body:dict):
    p=load(pid)
    try:group,v=locate(p,body.get('variation_id'))
    except ValueError as e:raise HTTPException(400,str(e))
    b=dict(p['brief'],selected_ids=group.get('source_ids',p['brief']['selected_ids']))
    return start(pid,'virality-review',{'variation_id':v['id']})

@app.post('/api/projects/{pid}/spoiler-check')
def spoiler_checker(pid:str,body:dict):
    p=load(pid)
    if not p['groups'] or p.get('pending_generation'):raise HTTPException(400,'Complete final promo drafts before running the batch spoiler checker.')
    return start(pid,'spoiler-check',body)

@app.post('/api/projects/{pid}/refine')
def refine(pid:str,body:dict):
    if not 1<=len(str(body.get('prompt','')).strip())<=2000:raise HTTPException(400,'Enter a prompt up to 2,000 characters.')
    return start(pid,'refine',body)
@app.get('/api/jobs/{jid}')
def job(jid:str):
    stored=job_store.get(jid)
    if not stored:raise HTTPException(404,'Job not found.')
    return stored

@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid:str):
    with gate:
        stored=job_store.get(jid)
        if not stored:raise HTTPException(404,'Job not found.')
        if stored['status'] in TERMINAL:return stored
        runtime=runtimes.get(jid)
        if not runtime:raise HTTPException(409,'This job has no local worker; restart the app to recover its interrupted status.')
        with runtime.lock:
            # A job may have finished while this endpoint waited for its checkpoint lock.
            if jobs[jid]['status'] in TERMINAL:return dict(jobs[jid])
            queued=jobs[jid]['status']=='queued'
            jobs[jid].update(status='cancelling',message='Stopping the local model request; saved outputs are preserved.')
            runtime.cancel()
            if queued:
                finish_job(jobs[jid],'cancelled','Stopped before starting. Completed saved outputs are preserved.')
                active.discard(jid)
                runtime.finished.set()
    runtime.finished.wait(3)
    return job(jid)
@app.post('/api/projects/{pid}/apply')
def apply(pid:str,body:dict):
    if active:raise HTTPException(409,'Wait for the current operation.')
    p=load(pid);vid=body.get('variation_id');proposal=p['proposals'].get(vid)
    if not proposal:raise HTTPException(404,'No pending proposal.')
    if not isinstance(proposal.get('variation'),dict):raise HTTPException(400,'No new draft was returned to keep.')
    v=copy.deepcopy(proposal['variation']);clear_review(v);group,_=locate(p,vid);b=p['brief'];ids=group.get('source_ids',b['selected_ids']);v.pop('spoiler_check',None);v['validation']=validate_cut(v,p['episodes'],ids,b['minimum'],b['maximum'],b['structure'])
    for g in p['groups']:
        g['variations']=[v if x['id']==vid else x for x in g['variations']]
    v['review_status']='unreviewed'
    del p['proposals'][vid];p.pop('spoiler_batch_check',None);return save(p)

@app.get('/api/projects/{pid}/handoff')
def handoff(pid:str,saved:bool=False):
    p=load(pid)
    return HTMLResponse(sheet(saved_output_view(p) if saved else p),headers={'Content-Disposition':'attachment; filename="promo-editor-handoff.html"'})

@app.get('/api/projects/{pid}/discovery/{rid}/export.pdf')
def discovery_pdf(pid:str,rid:str):
    from pdf_export import build_pdf
    p=run_view(load(pid),rid)
    return Response(build_pdf(p),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="ranked-episode-promos.pdf"'})

@app.get('/api/projects/{pid}/export.pdf')
def export_pdf(pid:str,saved:bool=False):
    from pdf_export import build_pdf
    p=load(pid)
    return Response(build_pdf(saved_output_view(p) if saved else p),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="promo-studio-cuts.pdf"'})

@app.post('/api/projects/{pid}/discard')
def discard(pid:str,body:dict):
    if active:raise HTTPException(409,'Wait for the current operation.')
    p=load(pid);vid=body.get('variation_id');p['proposals'].pop(vid,None)
    try:
        group,v=locate(p,vid);b=p['brief'];ids=group.get('source_ids',b['selected_ids'])
        validation=validate_cut(v,p['episodes'],ids,b['minimum'],b['maximum'],b['structure'])
        if not validation['eligible']:
            clear_review(v);v['review_status']='needs_repair'
            validation=validate_cut(v,p['episodes'],ids,b['minimum'],b['maximum'],b['structure'])
            validation['warnings'].append('Kept the current cut; it still fails local cut checks and needs repair.')
        v['validation']=validation
    except ValueError:pass
    return save(p)
@app.get('/api/projects/{pid}/export')
def export(pid:str,saved:bool=False):
    p=load(pid)
    if saved:p=saved_output_view(p)
    ranked=[];unranked=[];failures=list(p.get('failures',[]))
    for g in p['groups']:
        fingerprints=set()
        for v in g['variations']:
            b=p['brief'];v['validation']=validate_cut(v,p['episodes'],g.get('source_ids',b['selected_ids']),b['minimum'],b['maximum'],b['structure'])
            fingerprint=json.dumps(v.get('segments'),sort_keys=True)
            fingerprints.add(fingerprint)
            if v['validation']['eligible']:
                (ranked if v['validation']['score'] is not None else unranked).append(dict(group=g['title'],**v))
            else:failures.append(dict(group=g['title'],variation=v))
    ranked.sort(key=lambda v:v['validation']['score'] if v['validation']['score'] is not None else -1,reverse=True)
    for n,v in enumerate(ranked):v['posting_priority']=n+1
    with db() as c:raw=[dict(stage=r['stage'],**json.loads(r['body'])) for r in c.execute('SELECT stage,body FROM raw WHERE project_id=?',(pid,))]
    return JSONResponse(dict(project=p['name'],brief=p['brief'],analysis=p['analysis'],ranked_variations=ranked,unranked_variations=unranked,failures=failures,episodes=p['episodes'],raw_outputs=raw,methodology='Five local cut checks determine cut eligibility. Optional independent clip-only AI review supplies ranking; 0–5 rubric weights 25/25/15/25/10. Editorial potential is not measured virality. Script timing requires footage confirmation.'),headers={'Content-Disposition':'attachment; filename="promo-studio-cuts.json"'})

install_discovery(app,get_project,save,start,state,spent,lambda:bool(active))

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/')
def index():return FileResponse(ROOT/'static'/'index.html')
if __name__=='__main__':
    import uvicorn,sys,webbrowser
    if '--open' in sys.argv:threading.Timer(2,lambda:webbrowser.open('http://127.0.0.1:8787')).start()
    uvicorn.run(app,host='127.0.0.1',port=8787,access_log=False)
