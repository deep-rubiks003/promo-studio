"""One-draft episode grouping and optional spoiler review."""
import copy
import hashlib
import json
import time
import uuid
from parser import validate_cut
from prompt_payload import SOURCE_FORMAT, payload_json, draft_context

DRAFT = '''A promo has angle (short title), distinction (creative intent), segments:[{episode_id,start,end}] in numeric seconds, beats:[{role:"Hook"|"Context"|"Escalation"|"Cliffhanger",event_ids:[exact source event IDs],reason}], question (left unanswered), reason (posting rationale), weakness. Include all four beats in playback order. Hook within first3 seconds. Entire overlapping dialogue turns must fit inside boundaries. Cite only entire events contained in the cut. Do not require a complete emotional arc: select an understandable excerpt with minimal setup, rising tension and an unresolved turn. Stop before the payoff; never extend beyond the duration limit to complete a scene. Follow ONE causal conflict; do not stitch unrelated subplots merely to include all assigned episodes. An assigned source pool is permission, not a requirement to use every episode. Include source lines that connect any scene change. Leave the final conflict unresolved within the included dialogue. Do not invent or rewrite dialogue, voiceovers, events or timestamps. Continuous means exactly one segment; stitched permits up to8 ordered ranges. Respect the requested 20–60 second duration. Produce exactly ONE draft, never alternatives or variations. If no supported cut exists return {error:string}.'''

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
    reasons += ['Reviewer: '+v['review_notes']] if v.get('review_notes') else []
    reasons += ['Story assessment failed: '+k for k,value in (v.get('narrative') or {}).items() if value is False]
    return reasons

EDITORIAL_BRIEF = """Select existing show moments for a newcomer scrolling Instagram, TikTok or YouTube; do not summarize or write a trailer. No editing, music, text overlays, invented voiceover or rewritten dialogue/events/timestamps.
Compare plausible openings/endings. Do not require a complete emotional arc: select an understandable excerpt with minimal setup, rising tension and an unresolved turn. Stop before the payoff; never extend beyond the duration limit to complete a scene. Prefer continuous when equally strong; stitch only source-connected moments with intelligible transitions, never unrelated subplots. The assigned pool permits but does not require every episode.
Apply this rubric while choosing the FIRST draft:
- Hook (25%): a specific audible line or visible action earns attention in the first 3 seconds; avoid slow setup. A slap, reveal or shocking line is an example, not mandatory.
- Standalone clarity (25%): include only the relationships, stakes and conflict needed to understand THIS moment; omit backstory. Explanations and unseen episodes cannot fill gaps.
- Escalation (15%): the situation develops after the hook; stakes rise or expectations reverse. Avoid static quote collections.
- Cliffhanger (25%): end at a meaningful unresolved turn, threat, decision or revelation, before its consequence/answer. Metadata cannot create suspense already resolved in the clip.
- Cut quality (10%): intelligible start/end, complete dialogue turns across ALL overlapping tracks, preserved meaning/causal order, no misleading joins. Each timestamped dialogue block is independent; adjacent blocks by the same speaker need not remain together.
Check boundaries and duration BEFORE returning: continuous=exactly one segment; stitched=up to 8 ordered segments. Total duration is SUM(end-start), not last-source-time minus first-source-time; fit the brief within 20–60 seconds. Rechoose a window instead of shortening spoken sentences.
Cite four ordered beats: Hook, Context, Escalation, Cliffhanger, using exact source event IDs fully contained in their segments. Hook evidence begins within 3 seconds of playback. Every transition must make sense to a stranger.
Return {error:string} only if no supported cut exists. Never self-award validation, narrative approval or rubric scores; an independent reviewer judges the actual clip.
"""

DRAFT_OUTPUT = ' Return JSON {title,opportunity,promo:{angle,distinction,segments:[{episode_id,start,end}],beats:[{role,event_ids,reason}],question,reason,weakness}}. Beat reasons explain hook/cliffhanger source evidence; distinction is creative intent; question stays unanswered; reason is posting rationale; weakness is an honest tradeoff.'
SOURCE_IDS = ' episode_id MUST be the exact source episode_id (or id) string, NEVER an episode number. event_ids MUST be exact listed IDs. Return exactly ONE draft, never variations.'


def generation_prompt(scoped,selected,material,repair=None):
    feedback=(' Replace this rejected draft and address its review_feedback: '+payload_json(draft_context(repair))) if repair else ''
    return ('Create ONE promo from this assigned source pool. '+EDITORIAL_BRIEF+DRAFT_OUTPUT+SOURCE_IDS+
            feedback+SOURCE_FORMAT+' Brief:'+payload_json(scoped)+' ONLY SOURCE:'+payload_json(material(selected)))


def refinement_prompt(old,brief,selected,material,part,direction):
    return ('Propose an existing-source recut. Return ONE JSON object with exactly these outer keys: variation, change, tradeoff. '
            'variation MUST be a nested OBJECT, never a title string; all cut fields belong inside it. '
            'Shape (illustrative times only; use actual source bounds): '+payload_json(REFINEMENT_FORMAT)+' '+
            EDITORIAL_BRIEF+SOURCE_IDS+' Preserve untargeted parts where possible. Address current review_feedback, '
            'especially failed source/story checks. If the direction lacks source support, explain the tradeoff or return error; never invent. '+
            SOURCE_FORMAT+' Target:'+str(part)+' Request:'+str(direction)+' Current:'+payload_json(draft_context(old))+
            ' Brief:'+payload_json({k:brief[k] for k in ('minimum','maximum','structure','emphasis','selected_ids') if k in brief})+
            ' Source:'+payload_json(material(selected)))


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
            result=call(p['id'],stage,generation_prompt(scoped,selected,material,repair))
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
        if v.get('review_status') not in ('reviewed','needs_repair','unreviewed'):
            job['message']=f'Reviewing promo {i+1} of {len(brief["episode_groups"])}'
            normalize_sources(v,p['episodes'],ids)
            review(p,v,scoped)
            v['review_status']=v.get('review_status') if v.get('review_status')=='unreviewed' else ('reviewed' if v.get('narrative') and v.get('rubric') else 'needs_repair');save(p)
        if resume and len(brief['episode_groups'])==1 and not v.get('validation',{}).get('eligible'):
            # One correction attempt per group per explicit run; never spin on rejection.
            result,repaired=candidate(v)
            history=groups[i].setdefault('rejected_drafts',[]);history.append(copy.deepcopy(v))
            groups[i]['rejected_drafts']=history[-5:]
            groups[i]['variations']=[repaired];save(p)
            job['message']=f'Checking repaired promo {i+1} of {len(brief["episode_groups"])}'
            review(p,repaired,scoped)
            repaired['review_status']=repaired.get('review_status') if repaired.get('review_status')=='unreviewed' else ('reviewed' if repaired.get('narrative') and repaired.get('rubric') else 'needs_repair');save(p)
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
    result=call(p['id'],'spoiler-check','Check ALL final promo drafts together in ONE assessment. For each draft use ONLY its source_ids; other promos are not evidence about its story. Distinguish necessary premises from exposed outcomes, twists, identity reveals and answered cliffhangers. Follow each draft playback order. Do not invent future or unselected context. Return JSON {results:[{promo_id,status:"clear"|"warning"|"inconclusive",summary,findings:[{severity:"high"|"medium"|"low",kind,event_ids:[exact IDs including evidence IN THAT PROMO],explanation,suggestion}]}]}. Return exactly one result per promo.'+SOURCE_FORMAT+' FINAL PROMOS:'+payload_json(inputs)+' SOURCE CONTEXT (deduplicated):'+payload_json(material(episodes)))
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
    result=call(p['id'],'spoiler-check','Act as a Spoiler checker after a promo draft. Use ONLY assigned selected episodes. Distinguish a necessary premise/hook from a major twist, identity reveal, outcome, or resolution that gives away the reason to watch. Check whether the draft itself answers its promised unanswered question. Do not flag every conflict reveal as a spoiler. Do not infer anything from unselected or future episodes. Judge the draft in the listed segment/playback order, not episode order; source timestamps and playback timestamps are separate. This is editorial risk assessment, never a guarantee. Return JSON {status:"clear"|"warning"|"inconclusive",summary:string,findings:[{severity:"high"|"medium"|"low",kind:string,event_ids:[exact IDs including evidence IN THE PROMO],explanation:string,suggestion:string}]}. No new dialogue. Draft question:'+str(v.get('question',''))+' ORDERED SEGMENTS AND BEATS:'+payload_json(dict(segments=v['segments'],beats=v.get('beats',[])))+SOURCE_FORMAT+' PROMO EVENTS:'+payload_json(clip)+' SELECTED EPISODE CONTEXT:'+payload_json(material(episodes)))
    if result.get('status') not in ('clear','warning','inconclusive') or not isinstance(result.get('findings'),list) or not isinstance(result.get('summary'),str):raise ValueError('Spoiler checker returned an incomplete response. Previous check is preserved.')
    findings=[];unverified=False
    for f in result['findings']:
        if not isinstance(f,dict) or not isinstance(f.get('event_ids'),list):unverified=True;continue
        cited=f['event_ids']
        if not cited or any(not isinstance(i,str) or i not in events for i in cited) or not set(cited)&clipids:unverified=True;continue
        findings.append(dict(severity=f.get('severity') if f.get('severity') in ('high','medium','low') else 'medium',kind=str(f.get('kind','Possible spoiler')),explanation=str(f.get('explanation','')),suggestion=str(f.get('suggestion','')),event_ids=cited,events=[events[i] for i in cited]))
    status='inconclusive' if unverified else 'warning' if findings else 'inconclusive' if result['status']!='clear' else 'clear'
    return dict(status=status,summary=result['summary'],findings=findings,source_ids=ids,draft_fingerprint=fingerprint(v),checked_at=time.time(),limitation='AI assessment against the assigned selected episodes only. Unselected episodes and footage are not checked.')
