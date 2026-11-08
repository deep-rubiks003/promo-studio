"""One-draft episode grouping and optional spoiler review."""
import copy
import hashlib
import json
import time
import uuid
from parser import validate_cut

DRAFT = '''A promo has angle (short title), distinction (creative intent), segments:[{episode_id,start,end}] in numeric seconds, beats:[{role:"Hook"|"Context"|"Escalation"|"Cliffhanger",event_ids:[exact source event IDs],reason}], question (left unanswered), reason (posting rationale), weakness. Include all four beats in playback order. Hook within first3 seconds. Entire overlapping dialogue turns must fit inside boundaries. Cite only entire events contained in the cut. Create a coherent mini-story understandable without other episodes. Follow ONE causal conflict; do not stitch unrelated subplots merely to include all assigned episodes. An assigned source pool is permission, not a requirement to use every episode. Include source lines that connect any scene change. Leave the final conflict unresolved within the included dialogue. Do not invent or rewrite dialogue, voiceovers, events or timestamps. Continuous means exactly one segment; stitched permits up to8 ordered ranges. Respect the requested 20–60 second duration. Produce exactly ONE draft, never alternatives or variations. If no supported cut exists return {error:string}.'''

def build_brief(p,body):
    ids=body.get('selected_ids');known={e['id'] for e in p['episodes']}
    if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)) or not set(ids)<=known:raise ValueError('Select valid, distinct source episodes.')
    mode=body.get('promo_mode','single')
    if mode not in ('single','multiple'):raise ValueError('Choose one promo or multiple promos.')
    groups=body.get('episode_groups')
    if groups is None:groups=[ids] if mode=='single' else [[i] for i in ids]
    if not isinstance(groups,list) or not 1<=len(groups)<=10 or any(not isinstance(g,list) or not g or any(not isinstance(i,str) for i in g) for g in groups):raise ValueError('Choose 1–10 non-empty promo groups. Each group produces one draft.')
    flat=[i for g in groups for i in g]
    if len(flat)!=len(set(flat)) or set(flat)!=set(ids):raise ValueError('Assign every selected episode to exactly one promo group.')
    if mode=='single' and (len(groups)!=1 or set(groups[0])!=set(ids)):raise ValueError('One promo must use the selected episode pool as one group.')
    lo=float(body.get('minimum',20));hi=float(body.get('maximum',60));structure=body.get('structure','continuous')
    if not 20<=lo<=hi<=60 or structure not in ('continuous','stitched'):raise ValueError('Use a 20–60 second range and continuous or stitched structure.')
    return dict(schema_version=2,selected_ids=ids,promo_mode=mode,episode_groups=groups,group_count=len(groups),minimum=lo,maximum=hi,structure=structure,emphasis=str(body.get('emphasis',''))[:500])

def locate(p,vid):
    for g in p['groups']:
        for v in g['variations']:
            if v['id']==vid:return g,v
    raise ValueError('Promo draft not found.')

REFINEMENT_FORMAT = {
    'variation': {
        'angle':'short title', 'distinction':'creative intent',
        'segments':[{'episode_id':'exact source episode_id string','start':0.0,'end':25.0}],
        'beats':[{'role':role,'event_ids':['exact source event ID'],'reason':'why this beat works'} for role in ('Hook','Context','Escalation','Cliffhanger')],
        'question':'the question left unanswered','reason':'posting rationale','weakness':'editorial tradeoff'
    },
    'change':'what changed from the current cut','tradeoff':'what the change sacrifices'
}

def normalize_refinement(result):
    """Normalize observed complete layouts without inventing missing cut content."""
    if not isinstance(result,dict):raise ValueError('Gemini returned an invalid refinement object. Your current cut is unchanged.')
    if result.get('error'):
        reason=result['error'] if isinstance(result['error'],str) else 'No supported source recut was found.'
        raise ValueError('No supported refinement: '+reason+' Your current cut is unchanged.')
    v=result.get('variation')
    if isinstance(v,dict):
        v=copy.deepcopy(v)
    elif isinstance(v,(str,type(None))) and isinstance(result.get('segments'),list) and isinstance(result.get('beats'),list):
        v={k:copy.deepcopy(result[k]) for k in ('angle','distinction','segments','beats','question','reason','weakness') if k in result}
    else:
        raise ValueError('Gemini returned a refinement description without a complete source cut. Your current cut is unchanged; try a source-based direction.')
    for key in ('angle','distinction','question'):
        if not isinstance(v.get(key),str) or not v[key].strip():
            raise ValueError('Refinement is missing '+key+'. Your current cut is unchanged.')
    if not isinstance(v.get('segments'),list) or not v['segments'] or not all(isinstance(s,dict) for s in v['segments']):
        raise ValueError('Refinement has no usable source segments. Your current cut is unchanged.')
    if not isinstance(v.get('beats'),list) or not all(isinstance(b,dict) for b in v['beats']):
        raise ValueError('Refinement has invalid story beats. Your current cut is unchanged.')
    for key in ('change','tradeoff'):
        if not isinstance(result.get(key),str) or not result[key].strip():
            raise ValueError('Refinement is missing its '+key+' explanation. Your current cut is unchanged.')
    for key in ('validation','narrative','rubric','review_status','spoiler_check'):
        v.pop(key,None)  # Model-supplied approval never replaces independent validation.
    return dict(variation=v,change=result['change'],tradeoff=result['tradeoff'])


def normalize_sources(v, episodes, ids):
    """Resolve only exact IDs or unambiguous selected episode-number aliases."""
    corrections=[]
    selected=[ep for ep in episodes if ep['id'] in ids]
    known={ep['id'] for ep in selected}
    for segment in v.get('segments',[]) if isinstance(v.get('segments'),list) else []:
        if not isinstance(segment,dict):continue
        ref=segment.get('episode_id')
        if isinstance(ref,str) and ref in known:continue
        if isinstance(ref,bool) or not isinstance(ref,(str,int)):continue
        matches=[ep for ep in selected if str(ep['number'])==str(ref)]
        if len(matches)==1:
            segment['episode_id']=matches[0]['id']
            corrections.append(dict(from_reference=ref,episode_id=matches[0]['id']))
    if corrections:v.setdefault('source_reference_corrections',[]).extend(corrections)
    return v

def rejected_checks(v):
    reasons=[c['name']+': '+c['detail'] for c in v.get('validation',{}).get('checks',[]) if not c['passed']]
    return reasons

def generate_promos(p,brief,job,call,review,save,material,resume=False):
    pending=p.get('pending_generation') if resume else None
    if not pending or pending.get('brief')!=brief:
        pending=dict(brief=copy.deepcopy(brief),groups=[]);p['pending_generation']=pending;save(p)
    groups=pending['groups']
    pending.pop('state',None)
    for i,ids in enumerate(brief['episode_groups']):
        scoped={k:brief[k] for k in ('minimum','maximum','structure','emphasis') if k in brief}
        scoped['selected_ids']=ids
        selected=[e for e in p['episodes'] if e['id'] in ids]
        def candidate(repair=None):
            stage='candidate-repair' if repair else 'candidate-generation'
            job['message']=f'{"Repairing" if repair else "Drafting"} promo {i+1} of {len(brief["episode_groups"])}'
            instruction='Create ONE promo from this assigned source pool. '
            if repair:
                instruction+='Replace the rejected draft with ONE corrected cut. Failed checks: '+json.dumps(rejected_checks(repair))+' Previous draft: '+json.dumps(repair)+' '
            result=call(p['id'],stage,instruction+'Return JSON {title,opportunity,promo:promo}. '+DRAFT+' episode_id MUST be the exact string episode_id (or id) from ONLY SOURCE, NEVER the episode number. Every event_id must be an exact listed event ID. Continuous MUST have exactly ONE segment. Check end-start and total duration against Brief before responding. Brief:'+json.dumps(scoped)+' ONLY SOURCE:'+json.dumps(material(selected),ensure_ascii=False))
            if result.get('error'):result=dict(title=f'Promo {i+1}',promo=dict(angle=f'Promo {i+1} — no supported cut',segments=[],beats=[],question='',weakness=str(result['error'])))
            v=result.get('promo')
            if not isinstance(v,dict):
                old=result.get('groups',[])
                if old and isinstance(old[0],dict) and old[0].get('variations'):v=old[0]['variations'][0]
            if not isinstance(v,dict):raise ValueError('Gemini returned no source-backed promo draft.')
            v['id']=uuid.uuid4().hex;v['review_status']='pending'
            for key in ('spoiler_check','validation','narrative','rubric','review_notes'):v.pop(key,None)
            normalize_sources(v,p['episodes'],ids)
            return result,v
        if i>=len(groups):
            result,v=candidate()
            groups.append(dict(id=uuid.uuid4().hex,title=str(result.get('title') or v.get('angle') or f'Promo {i+1}'),opportunity=str(result.get('opportunity','')),source_ids=ids,variations=[v]));save(p)
        v=groups[i]['variations'][0]
        normalize_sources(v,p['episodes'],ids)
        check_only(p,v,scoped,clear=False);save(p)
    if p.get('groups'):
        p['previous_generation']=dict(groups=copy.deepcopy(p['groups']),brief=copy.deepcopy(p.get('brief')),saved_at=time.time())
    p['generation_summary']=dict(total=len(groups),passed=sum(bool(g['variations'][0]['validation']['eligible']) for g in groups),needs_repair=sum(not g['variations'][0]['validation']['eligible'] for g in groups))
    p.update(groups=copy.deepcopy(groups),brief=brief,proposals={},failures=[g['title']+': draft failed cut checks.' for g in groups if not g['variations'][0]['validation']['eligible']])
    p.pop('pending_generation',None)
    p.pop('spoiler_batch_check',None)

def fingerprint(v):
    return hashlib.sha256(json.dumps({k:v.get(k) for k in ('segments','beats','question')},sort_keys=True).encode()).hexdigest()

def check_all_spoilers(p,call,material):
    if p.get('pending_generation'):raise ValueError('Finish or resolve the pending generation before checking final output.')
    drafts=[(g,v) for g in p['groups'] for v in g['variations']]
    if not drafts:raise ValueError('Create final promo drafts first.')
    scope=lambda g:g.get('source_ids',p['brief']['selected_ids'])
    signature=hashlib.sha256(json.dumps([(v['id'],fingerprint(v),scope(g)) for g,v in drafts],sort_keys=True).encode()).hexdigest()
    if p.get('spoiler_batch_check',{}).get('fingerprint')==signature:return
    ids=set(i for g,_ in drafts for i in scope(g))
    episodes=[e for e in p['episodes'] if e['id'] in ids]
    inputs=[]
    for g,v in drafts:
        _,_,clip=spoiler_source(p,v,scope(g))
        inputs.append(dict(promo_id=v['id'],source_ids=scope(g),question=v.get('question'),segments=v['segments'],beats=v.get('beats',[]),ordered_events=clip))
    result=call(p['id'],'spoiler-check','Check ALL final promo drafts together in ONE assessment. For each draft use ONLY its source_ids; other promos are not evidence about its story. Distinguish necessary premises from exposed outcomes, twists, identity reveals and answered cliffhangers. Follow each draft playback order. Do not invent future or unselected context. Return JSON {results:[{promo_id,status:"clear"|"warning"|"inconclusive",summary,findings:[{severity:"high"|"medium"|"low",kind,event_ids:[exact IDs including evidence IN THAT PROMO],explanation,suggestion}]}]}. Return exactly one result per promo. FINAL PROMOS:'+json.dumps(inputs,ensure_ascii=False)+' SOURCE CONTEXT (deduplicated):'+json.dumps(material(episodes),ensure_ascii=False))
    rows=result.get('results')
    if not isinstance(rows,list) or any(not isinstance(r,dict) or not isinstance(r.get('promo_id'),str) for r in rows):raise ValueError('Incomplete batch spoiler response; previous checks are preserved.')
    byid={r['promo_id']:r for r in rows}
    if len(rows)!=len(byid) or set(byid)!={v['id'] for _,v in drafts}:raise ValueError('The batch did not check every promo exactly once; previous checks are preserved.')
    checks=[check_spoilers(p,v,scope(g),lambda *args:byid[v['id']],material) for g,v in drafts]
    for (_,v),check in zip(drafts,checks):v['spoiler_check']=check
    statuses=[r['status'] for r in checks]
    p['spoiler_batch_check']=dict(fingerprint=signature,checked_at=time.time(),count=len(checks),status='warning' if 'warning' in statuses else 'inconclusive' if 'inconclusive' in statuses else 'clear',results=[dict(promo_id=v['id'],**check) for (_,v),check in zip(drafts,checks)])

def spoiler_source(p,v,ids):
    episodes=[e for e in p['episodes'] if e['id'] in ids]
    events={e['id']:dict(e,episode_id=ep['id'],episode_number=ep['number'],episode_title=ep['title']) for ep in episodes for e in ep['events']}
    clip=[];offset=0
    for index,segment in enumerate(v['segments']):
        ep=next((e for e in episodes if e['id']==segment['episode_id']),None)
        if ep:
            contained=sorted((e for e in ep['events'] if e['start']>=segment['start'] and e['end']<=segment['end']),key=lambda e:e['start'])
            for e in contained:
                clip.append(dict(events[e['id']],segment_index=index,playback_start=round(offset+e['start']-segment['start'],3),playback_end=round(offset+e['end']-segment['start'],3)))
        offset+=segment['end']-segment['start']
    return episodes,events,clip

def check_spoilers(p,v,ids,call,material):
    episodes,events,clip=spoiler_source(p,v,ids)
    clipids={e['id'] for e in clip}
    result=call(p['id'],'spoiler-check','Act as a Spoiler checker after a promo draft. Use ONLY assigned selected episodes. Distinguish a necessary premise/hook from a major twist, identity reveal, outcome, or resolution that gives away the reason to watch. Check whether the draft itself answers its promised unanswered question. Do not flag every conflict reveal as a spoiler. Do not infer anything from unselected or future episodes. Judge the draft in the listed segment/playback order, not episode order; source timestamps and playback timestamps are separate. This is editorial risk assessment, never a guarantee. Return JSON {status:"clear"|"warning"|"inconclusive",summary:string,findings:[{severity:"high"|"medium"|"low",kind:string,event_ids:[exact IDs including evidence IN THE PROMO],explanation:string,suggestion:string}]}. No new dialogue. Draft question:'+str(v.get('question',''))+' ORDERED SEGMENTS AND BEATS:'+json.dumps(dict(segments=v['segments'],beats=v.get('beats',[])))+' PROMO EVENTS:'+json.dumps(clip,ensure_ascii=False)+' SELECTED EPISODE CONTEXT:'+json.dumps(material(episodes),ensure_ascii=False))
    if result.get('status') not in ('clear','warning','inconclusive') or not isinstance(result.get('findings'),list) or not isinstance(result.get('summary'),str):raise ValueError('Spoiler checker returned an incomplete response. Previous check is preserved.')
    findings=[];unverified=False
    for f in result['findings']:
        if not isinstance(f,dict) or not isinstance(f.get('event_ids'),list):unverified=True;continue
        cited=f['event_ids']
        if not cited or any(not isinstance(i,str) or i not in events for i in cited) or not set(cited)&clipids:unverified=True;continue
        findings.append(dict(severity=f.get('severity') if f.get('severity') in ('high','medium','low') else 'medium',kind=str(f.get('kind','Possible spoiler')),explanation=str(f.get('explanation','')),suggestion=str(f.get('suggestion','')),event_ids=cited,events=[events[i] for i in cited]))
    status='inconclusive' if unverified else 'warning' if findings else 'inconclusive' if result['status']!='clear' else 'clear'
    return dict(status=status,summary=result['summary'],findings=findings,source_ids=ids,draft_fingerprint=fingerprint(v),checked_at=time.time(),limitation='AI assessment against the assigned selected episodes only. Unselected episodes and footage are not checked.')


def clear_review(v):
    """A changed cut cannot inherit its previous optional assessments."""
    for key in ('narrative','rubric','review_notes','review_status','spoiler_check','rating','posting_priority'):
        v.pop(key,None)
    return v


def check_only(p,v,brief,clear=True):
    if clear:clear_review(v)
    normalize_sources(v,p['episodes'],brief['selected_ids'])
    v['validation']=validate_cut(v,p['episodes'],brief['selected_ids'],brief['minimum'],brief['maximum'],brief['structure'])
    if clear or v.get('review_status') not in ('reviewed','unreviewed'):
        v['review_status']='unreviewed' if v['validation']['eligible'] else 'needs_repair'
    return v


def repair_selection(p,variation_ids):
    """Validate the entire selection before a paid batch can start."""
    if not isinstance(variation_ids,list) or not variation_ids or any(not isinstance(i,str) for i in variation_ids) or len(set(variation_ids))!=len(variation_ids):
        raise ValueError('Select distinct failed promo drafts to repair.')
    selected=[]
    for vid in variation_ids:
        group,v=locate(p,vid)
        brief={k:p['brief'][k] for k in ('minimum','maximum','structure','emphasis') if k in p['brief']}
        brief['selected_ids']=group.get('source_ids',p['brief']['selected_ids'])
        validation=validate_cut(v,p['episodes'],brief['selected_ids'],brief['minimum'],brief['maximum'],brief['structure'])
        if validation['eligible']:raise ValueError('Only drafts that fail local cut checks can be selected for repair.')
        if vid in p.get('proposals',{}):raise ValueError('Decide on the pending proposal before repairing this draft again.')
        selected.append((group,v,brief,validation))
    return selected


def manual_bulk_repair(p,variation_ids,job,call,save,material):
    selected=repair_selection(p,variation_ids)
    batch=dict(variation_ids=list(variation_ids),items=[],status='running');p['manual_repair_batch']=batch;save(p)
    for index,(group,old,brief,validation) in enumerate(selected):
        item=dict(variation_id=old['id'],status='running');batch['items'].append(item);save(p)
        job['message']=f'Proposing repair {index+1} of {len(selected)}'
        previous=copy.deepcopy(old);previous['validation']=validation
        scoped=[e for e in p['episodes'] if e['id'] in brief['selected_ids']]
        try:
            result=call(p['id'],'manual-repair-proposal','Repair ONE failed promo using ONLY this assigned source pool. Return {variation:OBJECT,change:string,tradeoff:string}. '+DRAFT+' Shape:'+json.dumps(REFINEMENT_FORMAT)+' Failed LOCAL checks:'+json.dumps(rejected_checks(previous))+' Current:'+json.dumps(previous)+' Brief:'+json.dumps(brief)+' ONLY SOURCE:'+json.dumps(material(scoped),ensure_ascii=False))
            proposal=normalize_refinement(result);v=proposal['variation'];v['id']=old['id'];check_only(p,v,brief)
            proposal.update(status='pending',origin='manual_bulk_repair',created_at=time.time())
            p.setdefault('proposals',{})[old['id']]=proposal
            item.update(status='proposed',eligible=v['validation']['eligible'])
            save(p)
        except Exception as exc:
            # Provider, budget and cancellation failures stop immediately; schema
            # failures belong to this draft and allow the remaining batch to run.
            from job_runtime import JobCancelled
            stop=isinstance(exc,JobCancelled) or hasattr(exc,'http_status') or any(t in str(exc).lower() for t in ('spending limit','quota','authentication','api key','cancelled','connection'))
            stages=copy.deepcopy(job.get('stages',[]))
            if stages and stages[-1].get('status')=='running':
                stages[-1].update(status='error',error=str(exc),finished_at=time.time());job['stages']=stages
            item.update(status='paused' if stop else 'failed',error=str(exc));batch['status']='paused' if stop else 'running'
            if isinstance(exc,JobCancelled):raise
            save(p)
            if stop:raise
    batch['status']='done';save(p)
