"""Bounded, source-first show discovery. No provider or network dependencies.

Windows retain complete source events; a turn larger than the configured budget
fails explicitly instead of truncating it. Coverage describes completed scans,
not a claim that every possible editorial opportunity has been found.
"""
import copy
import hashlib
import json
import math
import time
import uuid

from models import model_rates
from parser import WEIGHTS, validate_cut
from workflow import DRAFT, normalize_sources

VERSION = 1
MAX_PROMPT_BYTES = 64000
WINDOW_BYTES = 20000
BATCH_SOURCE_BYTES = 48000
MAX_WINDOWS_PER_BATCH = 5
MAX_RESPONSE_BYTES = 24000
CANDIDATES_PER_BATCH = 3
OPPORTUNITIES_PER_BATCH = 8
REVIEW_BATCH_SIZE = 5
MAX_SPLIT_DEPTH = 3
FULL_SHOW_POLICY = ('Every independently qualified, distinct cut is ranked by editorial score. '
                    'No quantity cap or diversity-based stopping rule is applied. '
                    'Scores are editorial heuristics, not calibrated performance predictions.')
MAX_WINDOW_SECONDS = 180
OVERLAP_SECONDS = 60
EDITORIAL_CHECKS = {'Standalone story & unresolved ending', 'Complete editorial review'}
NARRATIVE_KEYS = ('standalone', 'escalation', 'cliffhanger', 'coherent')
SHORTLIST_FLOOR = 70
STORY_CRITERION_FLOOR = 3
SHORTLIST_POLICY = ('Editorial heuristic, not a calibrated performance prediction: independently eligible cuts '
                    'need at least 70/100 and standalone clarity and cliffhanger scores of at least 3/5. '
                    'Automatic quantity stops when the next diversity-adjusted score is below 70; '
                    'a requested cap accepts up to its limit subject to the same base quality floors. '
                    'Diversity adjustment subtracts 3 points per prior selection sharing a source episode '
                    'and up to 15 points for source overlap.')


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False)


def _bytes(value):
    return len(_json(value).encode('utf-8'))


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _source_fingerprint(episodes):
    return _hash([dict(id=ep['id'], duration=ep['duration'], events=ep['events']) for ep in episodes])


def plan_discovery(p, body, model):
    """Pure planning and validation. All estimates are conservative byte bounds."""
    if not isinstance(model, str):
        raise ValueError('Choose a supported model from the model selector.')
    model_rates(model)
    if not isinstance(body, dict):
        raise ValueError('Choose discovery settings.')
    ids = ([ep['id'] for ep in p['episodes']] if body.get('quantity') == 'all'
           else body.get('selected_ids'))
    known = {ep['id'] for ep in p['episodes']}
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids)) or not set(ids) <= known):
        raise ValueError('Select valid, distinct source episodes.')
    lo, hi = body.get('minimum', 20), body.get('maximum', 60)
    if not all(_number(x) for x in (lo, hi)) or not 20 <= lo <= hi <= 60:
        raise ValueError('Use a finite 20–60 second duration range.')
    structure = body.get('structure', 'continuous')
    quantity = body.get('quantity', 'auto')
    full_show = quantity == 'all'
    per_episode = body.get('per_episode', False)
    if type(per_episode) is not bool or (per_episode and not full_show):
        raise ValueError('Per-episode discovery requires full-show scope.')
    limit = body.get('limit', 30)
    if structure not in ('continuous', 'stitched', 'either'):
        raise ValueError('Choose continuous, stitched or either structure.')
    if quantity not in ('auto', 'cap', 'all') or (not full_show and (type(limit) is not int or not 1 <= limit <= 30)):
        raise ValueError('Choose automatic quantity or an upper cap of 1–30 promos.')
    emphasis = body.get('emphasis', '')
    if not isinstance(emphasis, str) or len(emphasis) > 500:
        raise ValueError('Keep the discovery direction within 500 characters.')
    if full_show:
        ids = [ep['id'] for ep in p['episodes']]
    selected = [ep for ep in p['episodes'] if ep['id'] in ids]
    windows = []
    for ep in selected:
        events = sorted(ep['events'], key=lambda e: (e['start'], e['end'], e['id']))
        if not events:
            raise ValueError('Selected episodes must contain timestamped source events.')
        index = 0
        while index < len(events):
            end = index
            while end < len(events):
                proposal = events[index:end+1]
                if _bytes(proposal) > WINDOW_BYTES:
                    break
                if end > index and events[end]['start'] - events[index]['start'] > MAX_WINDOW_SECONDS:
                    break
                end += 1
            if end == index:
                raise ValueError('A complete source turn is too large for the discovery context budget; no text was truncated.')
            rows = copy.deepcopy(events[index:end])
            windows.append(dict(id=f"{ep['id']}:window:{index}:{end}", episode_id=ep['id'],
                                events=rows, start=min(e['start'] for e in rows),
                                end=max(e['end'] for e in rows)))
            if end == len(events):
                break
            # Include whole preceding turns, including long dialogue overlapping
            # the new boundary. Forced progress keeps dense overlaps finite.
            boundary = events[end]['start'] - OVERLAP_SECONDS
            overlap = next((i for i in range(index+1, end) if events[i]['end'] >= boundary), end)
            index = max(index+1, overlap)
    batches = []
    current = []
    lookup = {w['id']: w for w in windows}
    for window in windows:
        proposed = current + [window['id']]
        if current and (len(proposed) > (1 if full_show else MAX_WINDOWS_PER_BATCH)
                        or _bytes([lookup[i] for i in proposed]) > BATCH_SOURCE_BYTES):
            batches.append(dict(id=f'batch-{len(batches)+1}', window_ids=current))
            current = []
        current.append(window['id'])
    if current:
        batches.append(dict(id=f'batch-{len(batches)+1}', window_ids=current))
    stitch = int(structure in ('stitched', 'either') and not per_episode)
    scan_bound = len(batches) * (2**(MAX_SPLIT_DEPTH+1)-1 if full_show else 1)
    candidate_bound = (len(batches) * (2**MAX_SPLIT_DEPTH if full_show else 1) + stitch) * CANDIDATES_PER_BATCH
    # A large clip may occupy a review request by itself, so ceil(n/5) is
    # not a safe upper bound. No repair/retry loop is performed here.
    maximum = scan_bound if per_episode else scan_bound + stitch + candidate_bound * (2 if full_show else 1)
    return dict(schema_version=VERSION, selected_ids=list(ids), structure=structure,
                quantity=quantity, limit=None if full_show else limit, full_show=full_show, per_episode=per_episode,
                episode_scope=[dict(episode_id=ep['id'], number=ep['number'], title=ep.get('title','')) for ep in selected], minimum=float(lo), maximum=float(hi),
                emphasis=emphasis, model=model, windows=windows, batches=batches,
                source_fingerprint=_source_fingerprint(selected),
                source_bytes=sum(_bytes(ep['events']) for ep in selected),
                total_windows=len(windows), batch_count=len(batches),
                candidate_upper_bound=candidate_bound,
                estimated_calls=dict(minimum=len(batches), maximum=maximum),
                estimated_max_input_bytes=maximum*MAX_PROMPT_BYTES,
                max_prompt_bytes=MAX_PROMPT_BYTES, max_response_bytes=MAX_RESPONSE_BYTES,
                coverage_limitation='Every selected source event is assigned to a scan window. Completed coverage is not exhaustive discovery; source timestamps and footage are unverified.')


def new_run(plan):
    run = dict(id=uuid.uuid4().hex, status='pending', phase='scan',
                created_at=time.time(), plan=copy.deepcopy(plan),
                coverage=[dict(window_id=w['id'], episode_id=w['episode_id'], status='pending') for w in plan['windows']],
                completed_batches=[], candidates=[], opportunities=[], shortlist=[],
                rejections=[], review_cursor=0, failures=[], stitching_done=False)
    _progress(run)
    return run


def _scope(plan):
    scope = {k: plan[k] for k in ('model', 'minimum', 'maximum', 'structure', 'emphasis')}
    if plan.get('per_episode'):
        scope['per_episode'] = True
        scope['local_check_policy'] = 2
    return scope


def _prompt(stage, plan, windows, opportunities=None):
    request = ('Scan EVERY supplied source window for supported standalone promo opportunities, '
               'and draft the strongest candidates in this same response. An empty list is valid; '
               'never fill a quota. Return JSON {candidates:[draft],opportunities:[{key,reason,event_ids:[exact source IDs]}]}. '
               f'Return at most {CANDIDATES_PER_BATCH} candidates and {OPPORTUNITIES_PER_BATCH} opportunities; '
               f'keep the complete JSON under {MAX_RESPONSE_BYTES} UTF-8 bytes. '
               'Supplied windows may overlap; do not duplicate the same cut. '
               'Only source IDs and complete dialogue turns supplied below are allowed. '
               'Any rough_priority is scheduling advice, never an approved editorial score. '
               'Keep all reason fields concise (at most 200 characters); use exact source IDs instead of copying dialogue. ')
    if stage == 'discovery-stitch':
        request = ('Make at most ONE targeted stitching attempt using only the exact source windows below, '
                   'retrieved from linked opportunities. Return no candidates unless the included dialogue '
                   'supports one coherent standalone conflict with understandable transitions. '
                   'Do not invent a bridge or use unrelated plots. ' + request)
    if plan.get('per_episode'):
        request = request.replace('Scan EVERY supplied source window for supported standalone promo opportunities, and draft the strongest candidates in this same response. An empty list is valid; never fill a quota.',
                                  'Propose ONE best available promo draft from this episode window, even when its story or ending is weak. Explain actual weaknesses. Do not return an empty list merely because no ideal cliffhanger or strong standalone story exists. Return none only when no usable timestamped source cut exists; never invent source dialogue or times to meet a requirement.')
        request = request.replace(f'Return at most {CANDIDATES_PER_BATCH} candidates', 'Return at most ONE candidate')
    prompt = request + DRAFT.replace('Produce exactly ONE draft, never alternatives or variations.',
                                    ('Produce ONE best available actual source draft, explicitly noting unmet story or duration requirements; never invent material.'
                                     if plan.get('per_episode') else f'Produce up to {CANDIDATES_PER_BATCH} distinct drafts; return none if unsupported.'))
    if plan.get('per_episode'):
        prompt += (' Work on the single supplied episode only. Every candidate must stay entirely within this episode; '
                   'never combine different episodes. Candidates across its windows will be reviewed to choose ONE best promo for this episode.')
    prompt += ' SETTINGS:' + _json(_scope(plan))
    if opportunities:
        prompt += ' LINKED OPPORTUNITIES:' + _json(opportunities)
    return prompt + ' EXACT SOURCE WINDOWS:' + _json(windows)


def _call(p, plan, call, stage, prompt):
    if len(prompt.encode('utf-8')) > MAX_PROMPT_BYTES:
        raise ValueError('Discovery request exceeds its conservative context byte budget.')
    result = call(p['id'], stage, prompt)
    if not isinstance(result, dict) or _bytes(result) > MAX_RESPONSE_BYTES:
        raise ValueError('Discovery returned an invalid or oversized response.')
    return result


def _scan_response(result):
    if (not isinstance(result.get('candidates'), list) or not isinstance(result.get('opportunities'), list)
            or len(result['candidates']) > CANDIDATES_PER_BATCH
            or len(result['opportunities']) > OPPORTUNITIES_PER_BATCH):
        raise ValueError('Discovery must return bounded candidate and opportunity lists.')
    return result


def _validation(v, p, plan):
    structure = plan['structure']
    if structure == 'either':
        structure = 'continuous' if isinstance(v.get('segments'), list) and len(v['segments']) == 1 else 'stitched'
    selected_ids = [v['scoped_episode_id']] if plan.get('per_episode') and v.get('scoped_episode_id') else plan['selected_ids']
    return validate_cut(v, p['episodes'], selected_ids, plan['minimum'], plan['maximum'], structure)


def _clip(v, p):
    byid = {ep['id']: ep for ep in p['episodes']}
    rows = []
    offset = 0
    for segment in v['segments']:
        ep = byid[segment['episode_id']]
        events = [dict(e, playback_start=offset+e['start']-segment['start'],
                       playback_end=offset+e['end']-segment['start'])
                  for e in ep['events'] if segment['start']-.001 <= e['start'] and e['end'] <= segment['end']+.001]
        rows.append(dict(segment=copy.deepcopy(segment), events=events))
        offset += segment['end']-segment['start']
    return dict(promo_id=v['id'], question=v['question'], segments=copy.deepcopy(v['segments']), ordered_clip=rows)


def _review_prompt(clips):
    return ('Independently review ONLY each supplied clip as a newcomer, with no show or generator context. '
            'Assess standalone clarity, escalation, coherent transitions and a genuinely unresolved ending. '
            'The question is an editorial claim to test, not information given to a viewer. '
            'Assess every clip independently; another clip cannot supply missing context or clarity. '
            'Use the exact playback order. Do not infer unseen context or future episodes. '
            'Return JSON {results:[{promo_id,narrative:{standalone:boolean,escalation:boolean,cliffhanger:boolean,coherent:boolean},'
            'rubric:[{criterion,score,reason}],review_notes:string}]}. Return exactly one result per promo_id; '
            'five unique criteria: Hook, Standalone clarity, Escalation, Cliffhanger, Cut quality; '
            'scores finite 0–5 and source-based reasons required. CLIPS:' + _json(clips))


def _accept_scan(p, run, result, windows, batch_id):
    plan = run['plan']
    supplied = {e['id'] for w in windows for e in w['events']}
    episodes = {w['episode_id'] for w in windows}
    accepted = []
    for raw in result['candidates']:
        reason = None
        if not isinstance(raw, dict):
            run['rejections'].append(dict(stage=batch_id, reason='Malformed candidate object.'))
            continue
        v = {k: copy.deepcopy(raw[k]) for k in ('angle', 'distinction', 'segments', 'beats', 'question', 'reason', 'weakness', 'rough_priority') if k in raw}
        v.update(id=uuid.uuid4().hex, review_status='pending', discovery_batch=batch_id)
        if plan.get('per_episode'):
            v['scoped_episode_id'] = next(iter(episodes))
        normalize_sources(v, p['episodes'], plan['selected_ids'])
        if not plan.get('per_episode') and any(not isinstance(v.get(k), str) or not v[k].strip() for k in ('angle', 'distinction', 'question')):
            reason = 'Candidate is missing its angle, intent or unanswered question.'
        validation = _validation(v, p, plan)
        failures = [c['name']+': '+c['detail'] for c in validation['checks'] if not c['passed'] and c['name'] not in EDITORIAL_CHECKS]
        if failures:
            reason = '; '.join(failures)
        if not reason and not plan.get('per_episode'):
            segs = v['segments']
            signatures = [(s['episode_id'], s['start'], s['end']) for s in segs]
            if plan.get('per_episode') and len({s['episode_id'] for s in segs}) != 1:
                reason = 'Per-episode promo must use only its own episode.'
            elif len(signatures) != len(set(signatures)):
                reason = 'Repeated identical source segments are not a distinct story.'
            elif any(s['episode_id'] not in episodes for s in segs):
                reason = 'Candidate uses an episode outside this source window batch.'
            else:
                byid = {ep['id']: ep for ep in p['episodes']}
                touched = {e['id'] for s in segs for e in byid[s['episode_id']]['events']
                           if e['start'] < s['end'] and e['end'] > s['start']}
                if not touched <= supplied:
                    reason = 'Candidate uses source events not supplied in its exact windows.'
        if not plan.get('per_episode') and not reason and len(_review_prompt([_clip(v, p)]).encode('utf-8')) > MAX_PROMPT_BYTES:
            reason = 'Candidate clip is too large for bounded independent review.'
        if reason:
            v.update(review_status='rejected', validation=validation)
            run['rejections'].append(dict(candidate_id=v['id'], stage=batch_id, reason=reason))
            if plan.get('per_episode'):
                run.setdefault('rejected_candidates', []).append(copy.deepcopy(v))
        else:
            v['validation'] = validation
            if plan.get('per_episode'):
                v['review_status'] = 'unreviewed'
            run['candidates'].append(v)
            accepted.append(v)
    for item in result['opportunities']:
        if (isinstance(item, dict) and isinstance(item.get('key'), str) and item['key'].strip()
                and isinstance(item.get('event_ids'), list) and item['event_ids']
                and all(isinstance(i, str) and i in supplied for i in item['event_ids'])):
            run['opportunities'].append(dict(key=item['key'][:200], reason=str(item.get('reason', ''))[:1000],
                                             event_ids=list(dict.fromkeys(item['event_ids'])), batch_id=batch_id))
    return accepted


def _overlap(a, b):
    """Intersection divided by the smaller source duration, across episodes."""
    def merged(v):
        result = {}
        for s in sorted(v['segments'], key=lambda x: (x['episode_id'], x['start'])):
            rows = result.setdefault(s['episode_id'], [])
            if rows and s['start'] <= rows[-1][1]:
                rows[-1][1] = max(rows[-1][1], s['end'])
            else:
                rows.append([s['start'], s['end']])
        return result
    x, y = merged(a), merged(b)
    duration = lambda d: sum(end-start for rows in d.values() for start, end in rows)
    da, db = duration(x), duration(y)
    intersection = sum(max(0, min(b1, b2)-max(a1, a2)) for ep in x.keys() & y.keys()
                       for a1, b1 in x[ep] for a2, b2 in y[ep])
    return intersection/min(da, db) if min(da, db) else 0, min(da, db)/max(da, db) if max(da, db) else 0


def _priority(v):
    value = v.get('rough_priority', 0)
    return value if _number(value) else 0


def _dedup(run):
    """Only exact ordered source cuts can skip an independent review."""
    kept = []
    seen = set()
    for v in sorted(run['candidates'], key=lambda v: (v.get('review_status') not in ('reviewed','review_failed'), -_priority(v))):
        key = tuple((s['episode_id'], s['start'], s['end']) for s in v['segments'])
        if key in seen:
            v['review_status'] = 'duplicate'
            run['rejections'].append(dict(candidate_id=v['id'], stage='dedup', reason='Exact duplicate of an existing ordered source cut.'))
        else:
            seen.add(key)
            kept.append(v)
    return [v['id'] for v in kept]


def _same_playback_order(a, b):
    """Overlapping source material in reverse playback order remains distinct."""
    def episode_order(v):
        result = []
        for segment in v['segments']:
            if not result or result[-1] != segment['episode_id']:
                result.append(segment['episode_id'])
        return result
    if episode_order(a) != episode_order(b):
        return False
    matches = [(i, j) for i, x in enumerate(a['segments']) for j, y in enumerate(b['segments'])
               if x['episode_id'] == y['episode_id'] and min(x['end'], y['end']) > max(x['start'], y['start'])]
    return not any(i < k and j > l for i, j in matches for k, l in matches)


def _targeted_windows(run):
    """One small deterministic exact-ID lookup, never a summary-only retrieval."""
    opportunities = run['opportunities']
    # Consume lazily: repeated opportunity keys across a hundred episodes
    # must not allocate a quadratic list of every possible stitching pair.
    pairs = ((a, b) for i, a in enumerate(opportunities) for b in opportunities[i+1:]
             if a['batch_id'] != b['batch_id'] and (a['key'].casefold() == b['key'].casefold()
                                                   or set(a['event_ids']) & set(b['event_ids'])))
    for a, b in pairs:
        ids = set(a['event_ids']) | set(b['event_ids'])
        selected = []
        remaining = set(ids)
        for w in run['plan']['windows']:
            if remaining & {e['id'] for e in w['events']}:
                proposed = selected+[w]
                if _bytes(proposed) <= BATCH_SOURCE_BYTES:
                    selected = proposed
                    remaining -= {e['id'] for e in w['events']}
        if not remaining and selected:
            links = [{k: row[k] for k in ('key', 'event_ids')} for row in (a, b)]
            if len(_prompt('discovery-stitch', run['plan'], selected, links).encode('utf-8')) <= MAX_PROMPT_BYTES:
                return selected, links
    return [], []


def _review_result(result, ids):
    rows = result.get('results')
    if (not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('promo_id'), str) for r in rows)
            or len(rows) != len(ids) or {r['promo_id'] for r in rows} != set(ids)):
        raise ValueError('Independent review must cover every promo ID exactly once; previous reviews are preserved.')
    for row in rows:
        narrative, rubric = row.get('narrative'), row.get('rubric')
        if (not isinstance(narrative, dict) or any(type(narrative.get(k)) is not bool for k in NARRATIVE_KEYS)
                or not isinstance(row.get('review_notes'), str) or not isinstance(rubric, list)
                or len(rubric) != len(WEIGHTS) or any(not isinstance(r, dict) for r in rubric)):
            raise ValueError('Independent review returned incomplete story or rubric results; previous reviews are preserved.')
        if ({r.get('criterion') for r in rubric if isinstance(r.get('criterion'), str)} != set(WEIGHTS)
                or any(not _number(r.get('score')) or not 0 <= r['score'] <= 5
                       or not isinstance(r.get('reason'), str) or not r['reason'].strip() for r in rubric)):
            raise ValueError('Independent review returned incomplete scoring results; previous reviews are preserved.')
    return {r['promo_id']: {k: copy.deepcopy(r[k]) for k in ('narrative', 'rubric', 'review_notes')} for r in rows}


def _rank(run, complete=False):
    if run['plan'].get('per_episode'):
        _rank_episodes(run)
        _progress(run, complete)
        return
    eligible = [v for v in run['candidates'] if v.get('review_status') == 'reviewed' and v.get('validation', {}).get('eligible')]
    remaining = []
    run['ranking_exclusions'] = []
    for v in eligible:
        scores = {r['criterion']: r['score'] for r in v['rubric']}
        if (v['validation']['score'] >= SHORTLIST_FLOOR
                and scores['Standalone clarity'] >= STORY_CRITERION_FLOOR
                and scores['Cliffhanger'] >= STORY_CRITERION_FLOOR):
            remaining.append(v)
        else:
            run['ranking_exclusions'].append(dict(candidate_id=v['id'], reason='Below the heuristic editorial shortlist floor.'))
    # Similar boundaries can change clarity and an ending completely. Decide
    # which near duplicate survives using independent quality, never the
    # generator's untrusted rough scheduling preference.
    distinct = []
    def quality(v):
        values = {r['criterion']: r['score'] for r in v['rubric']}
        return v['validation']['score'], values['Standalone clarity'], values['Cliffhanger']
    def near_duplicate(a, b):
        if not _same_playback_order(a, b):
            return False
        overlap, ratio = _overlap(a, b)
        return overlap >= .85 and ratio >= .65
    for v in sorted(remaining, key=quality, reverse=True):
        if any(near_duplicate(v, other) for other in distinct):
            run['ranking_exclusions'].append(dict(candidate_id=v['id'], reason='High-overlap source cut suppressed after independent review in favor of the stronger eligible cut.'))
        else:
            distinct.append(v)
    remaining = distinct
    if run['plan'].get('per_episode'):
        best_by_episode = {}
        for v in sorted(remaining, key=quality, reverse=True):
            eid = v['segments'][0]['episode_id']
            if eid in best_by_episode:
                run['ranking_exclusions'].append(dict(candidate_id=v['id'], reason='A stronger qualifying promo was selected for this episode.'))
            else:
                best_by_episode[eid] = v
        remaining = list(best_by_episode.values())
    selected = []
    adjustments = {}
    full_show = run['plan'].get('quantity') == 'all'
    limit = len(remaining) if full_show else run['plan']['limit']
    if full_show:
        selected = sorted(remaining, key=quality, reverse=True)
        adjustments = {v['id']: 0 for v in selected}
        remaining = []
    while remaining and len(selected) < limit:
        def rank(v):
            source = {s['episode_id'] for s in v['segments']}
            repeated = sum(bool(source & {s['episode_id'] for s in other['segments']}) for other in selected)
            overlap = max((_overlap(v, other)[0] for other in selected), default=0)
            clarity = next(r['score'] for r in v['rubric'] if r['criterion'] == 'Standalone clarity')
            penalty = 0 if full_show else repeated*3 + overlap*15
            return (v['validation']['score'] - penalty, clarity, v['validation']['score'])
        best = max(remaining, key=rank)
        adjusted = rank(best)[0]
        if run['plan']['quantity'] == 'auto' and adjusted < SHORTLIST_FLOOR:
            run['ranking_exclusions'].extend(dict(candidate_id=v['id'], reason='Automatic quantity stopped below the heuristic diversity-adjusted quality floor.') for v in remaining)
            break
        adjustments[best['id']] = best['validation']['score'] - adjusted
        remaining.remove(best)
        selected.append(best)
    def posting_reason(v):
        rubric = {r['criterion']: r for r in v['rubric']}
        reason = (f"Independent clip score {v['validation']['score']}/100. "
                  f"Hook: {rubric['Hook']['reason']} Cliffhanger: {rubric['Cliffhanger']['reason']}")
        penalty = adjustments[v['id']]
        if penalty:
            reason += f' Source repetition/overlap adjustment: -{penalty:.1f} points.'
        return reason
    run['shortlist'] = [dict(id=v['id'], title=v['angle'], opportunity=v.get('reason', v.get('distinction', '')),
                             source_ids=list(dict.fromkeys(s['episode_id'] for s in v['segments'])),
                             variations=[copy.deepcopy(v)], posting_priority=i+1,
                             ordered_source_ranges=copy.deepcopy(v['segments']),
                             hook=next(r['reason'] for r in v['rubric'] if r['criterion']=='Hook'),
                             cliffhanger=next(r['reason'] for r in v['rubric'] if r['criterion']=='Cliffhanger'),
                             posting_reason=posting_reason(v))
                        for i, v in enumerate(selected)]
    _progress(run, complete)





def _candidate_rating(v):
    validation = v.get('validation', {})
    score = validation.get('score')
    if not _number(score):
        score = validation.get('rubric_score')
    return score if _number(score) else None


def _candidate_reasons(run, v):
    reasons = [str(row['reason']) for row in run.get('rejections', []) if row.get('candidate_id')==v['id']]
    reasons.extend(str(c['name'])+': '+str(c.get('detail','')) for c in v.get('validation',{}).get('checks',[]) if not c.get('passed'))
    if v.get('review_notes'):
        reasons.append(str(v['review_notes']))
    return list(dict.fromkeys(reasons))


def _rank_episodes(run):
    """Rank one actual draft per episode, including failed drafts for repair."""
    candidates = run['candidates'] + run.get('rejected_candidates', [])
    selected = []
    run['ranking_exclusions'] = []
    for ep in run['plan']['episode_scope']:
        eid = ep['episode_id']
        own = [v for v in candidates if v.get('scoped_episode_id')==eid]
        if not own:
            continue
        def quality(v):
            passed = bool(v.get('validation',{}).get('eligible'))
            score = _candidate_rating(v)
            return passed, score if score is not None else -1, v.get('review_status')=='reviewed'
        choice = run.get('episode_choices',{}).get(eid)
        best = next((v for v in own if v['id']==choice),None) or max(own, key=quality)
        selected.append((ep, best, quality(best)))
        run['ranking_exclusions'].extend(dict(candidate_id=v['id'], reason='Another draft was selected as the best available for this episode.') for v in own if v is not best)
    selected.sort(key=lambda item:item[2], reverse=True)
    rows = []
    for i, (ep, v, quality) in enumerate(selected):
        passed = quality[0]
        rating = _candidate_rating(v)
        rubric = {r.get('criterion'):r for r in v.get('rubric',[]) if isinstance(r,dict)}
        reasons = _candidate_reasons(run, v)
        hook = str(rubric.get('Hook',{}).get('reason','Independent hook review unavailable.'))
        cliffhanger = str(rubric.get('Cliffhanger',{}).get('reason','Independent cliffhanger review unavailable.'))
        reason = (f"Independent clip rating {rating}/100. " if rating is not None else 'Independent rating unavailable. ')
        if passed:
            reason += (f'All local cut checks passed. Hook: {hook} Cliffhanger: {cliffhanger}' if rating is not None
                       else 'All local cut checks passed. Optional virality review has not been run.')
        else:
            reason += 'Needs repair before posting. '+('; '.join(reasons) or 'Independent source/story approval is incomplete.')
        segments = v.get('segments') if isinstance(v.get('segments'),list) else []
        rows.append(dict(id=v['id'], episode_id=ep['episode_id'], title=v.get('angle') or ep.get('title') or 'Episode promo draft',
                         opportunity=v.get('reason') or v.get('distinction',''), source_ids=[ep['episode_id']],
                         variations=[copy.deepcopy(v)], posting_priority=i+1, posting_reason=reason,
                         ordered_source_ranges=copy.deepcopy(segments), hook=hook, cliffhanger=cliffhanger,
                         eligible=bool(passed), status='ready' if passed else 'needs_repair', score=rating,
                         failed_checks=[copy.deepcopy(c) for c in v.get('validation',{}).get('checks',[]) if not c.get('passed')],
                         warnings=[] if passed else reasons))
    run['shortlist'] = rows


def project_episode_results(p, run):
    """Pure saved-result projection; never calls a provider or mutates storage."""
    view = copy.deepcopy(run)
    if view['plan'].get('per_episode'):
        if not view['plan'].get('episode_scope'):
            ids = set(view['plan']['selected_ids'])
            view['plan']['episode_scope'] = [dict(episode_id=ep['id'],number=ep['number'],title=ep.get('title','')) for ep in p['episodes'] if ep['id'] in ids]
        # Old saved candidates may predate explicit originating-episode tags.
        windows = {w['id']:w['episode_id'] for w in view['plan']['windows']}
        batches = {b['id']:{windows[wid] for wid in b['window_ids']} for b in view['plan']['batches']}
        for v in view['candidates'] + view.get('rejected_candidates',[]):
            if v.get('scoped_episode_id'):
                continue
            batch = v.get('discovery_batch','').split(':split')[0]
            ids = batches.get(batch,set())
            if len(ids)==1:
                v['scoped_episode_id'] = next(iter(ids))
            elif isinstance(v.get('segments'),list):
                sources = {row.get('episode_id') for row in v['segments'] if isinstance(row,dict)}
                if len(sources)==1:
                    v['scoped_episode_id'] = next(iter(sources))
        for v in view['candidates'] + view.get('rejected_candidates',[]):
            v['validation'] = _validation(v,p,view['plan'])
        _rank(view, bool(view.get('summary',{}).get('complete') or view.get('status')=='done'))
    return view


def _episode_results(run):
    """Keep an explicit outcome for every episode, including unsupported cuts."""
    ranked = {row['source_ids'][0]: row for row in run['shortlist']}
    outcomes = []
    candidates = run['candidates'] + run.get('rejected_candidates', [])
    for ep in run['plan']['episode_scope']:
        eid = ep['episode_id']
        coverage = [row for row in run['coverage'] if row['episode_id']==eid]
        unfinished = any(row['status'] in ('pending', 'partial') for row in coverage)
        own = [v for v in candidates if v.get('scoped_episode_id')==eid]
        pending_review = any(v.get('review_status')=='pending' for v in own)
        winner = ranked.get(eid)
        status = 'pending' if unfinished else ('ready' if winner and winner.get('eligible',True) else 'needs_repair')
        row = dict(ep, status=status, reasons=[], repair_available=True)
        if winner:
            row.update(draft=copy.deepcopy(winner['variations'][0]), posting_priority=winner['posting_priority'],
                       posting_reason=winner['posting_reason'], score=winner.get('score'),
                       eligible=winner.get('eligible',True), failed_checks=copy.deepcopy(winner.get('failed_checks',[])))
            if not winner.get('eligible',True):
                row['reasons'] = copy.deepcopy(winner.get('warnings',[]))
        elif own:
            def score(v):
                validation = v.get('validation', {})
                return (validation.get('score') or validation.get('rubric_score') or -1, _priority(v))
            best = max(own, key=score)
            row['draft'] = copy.deepcopy(best)
            row['reasons'] = [x['reason'] for x in run['rejections'] + run.get('ranking_exclusions', [])
                              if x.get('candidate_id')==best['id']]
            if not row['reasons'] and status=='needs_repair':
                row['reasons'] = ['No candidate passed the independent story and editorial quality checks.']
        elif status=='needs_repair':
            row['reasons'] = ['No supported candidate was found in the completed episode scans.']
        if any(x['status']=='failed' for x in coverage):
            row['reasons'].append('One or more source windows could not be scanned successfully.')
        attempts = [a for a in run.get('repair_attempts',[]) if a.get('episode_id')==eid]
        if attempts:
            row['last_repair'] = copy.deepcopy(attempts[-1])
        row['repair_available'] = not bool(row.get('draft',{}).get('validation',{}).get('eligible'))
        if eid in run.get('repair_proposals',{}):
            row['repair_proposal'] = copy.deepcopy(run['repair_proposals'][eid])
        outcomes.append(row)
    run['episode_results'] = outcomes


def _progress(run, complete=False):
    coverage = run['coverage']
    plan = run['plan']
    events = {e['id'] for w in plan['windows'] for e in w['events']}
    scanned = set()
    windows = {w['id']: w for w in plan['windows']}
    for row in coverage:
        if row['status'] in ('scanned', 'no_candidate'):
            window = windows[row['window_id']]
            scanned.update(e['id'] for e in window['events'])
        else:
            scanned.update(row.get('scanned_event_ids', []))
    if plan.get('per_episode'):
        _episode_results(run)
    run['summary'] = dict(
        scanned_windows=sum(c['status'] in ('scanned', 'no_candidate') for c in coverage),
        total_windows=len(coverage), scanned_events=len(scanned), total_events=len(events),
        failed_windows=sum(c['status']=='failed' for c in coverage),
        batches_completed=sum(b['id'] in run['completed_batches'] for b in plan['batches']),
        batches_total=len(plan['batches']), candidates=len(run['candidates']),
        reviewed=sum(v.get('review_status') == 'reviewed' for v in run['candidates']),
        failed_reviews=sum(v.get('review_status') == 'review_failed' for v in run['candidates']),
        eligible=sum(bool(v.get('validation', {}).get('eligible', False)) for v in run['candidates']),
        shortlisted=len(run['shortlist']), complete=bool(complete),
        ranked_episodes=len(run['shortlist']) if plan.get('per_episode') else None,
        total_episodes=len(plan.get('episode_scope',[])) if plan.get('per_episode') else None,
        episodes_with_drafts=len(run['shortlist']) if plan.get('per_episode') else None,
        ready_episodes=sum(r['status']=='ready' for r in run.get('episode_results',[])),
        needs_repair_episodes=sum(r['status']=='needs_repair' for r in run.get('episode_results',[])),
        rejected_candidate_count=len({r['candidate_id'] for r in run.get('rejections',[]) if r.get('candidate_id')}),
        rejection_event_count=len(run.get('rejections',[])),
        coverage_complete=len(scanned)==len(events) and all(c['status'] in ('scanned','no_candidate') for c in coverage),
        shortlist_policy=('One best available actual draft per episode, with locally valid cuts ranked first and failed cuts retained for repair; '
                          'optional ratings are editorial heuristics. Rejection counts refer to candidate cuts, not episodes.'
                          if plan.get('per_episode') else FULL_SHOW_POLICY if plan.get('quantity')=='all' else SHORTLIST_POLICY),
        limitation=plan['coverage_limitation'])


def _shape_error(exc):
    # Transport, quota, authentication, budget and cancellation errors must pause
    # immediately. Only output/schema failures justify smaller source requests.
    return isinstance(exc, ValueError) and not hasattr(exc, 'http_status') and any(word in str(exc).lower() for word in (
        'malformed json', 'invalid or oversized response', 'bounded candidate',
        'independent review', 'context byte budget', 'out of range float',
        'unexpected response shape', 'did not complete its response', 'returned no candidates'))


def _split_windows(source):
    if len(source) > 1:
        mid = len(source)//2
        return [source[:mid], source[mid:]]
    window = source[0]
    rows = window['events']
    if len(rows) < 2:
        return []
    mid = len(rows)//2
    # Keep complete turns. A small shared boundary keeps context without
    # permitting non-shrinking recursive requests.
    parts = [rows[:mid], rows[max(1,mid-1):]]
    if any(len(part)>=len(rows) for part in parts):
        parts = [rows[:mid], rows[mid:]]
    return [[dict(window, id=window['id']+f':part{i}', events=copy.deepcopy(part),
                  start=min(e['start'] for e in part), end=max(e['end'] for e in part))]
            for i, part in enumerate(parts)]


def _scan_task(p, run, call, save, source, batch_id, root_ids, depth=0):
    """Persist the split tree and each leaf so resuming never repeats success."""
    if batch_id in run['completed_batches']:
        return
    plan = run['plan']
    splits = run.setdefault('scan_splits', {})
    children = splits.get(batch_id)
    result = None
    if children is None:
        key = _hash(dict(version=VERSION, scope=_scope(plan), windows=source))
        result = p['discovery_cache']['scans'].get(key)
        try:
            if result is None:
                result = _scan_response(_call(p, plan, call, 'discovery-scan', _prompt('discovery-scan', plan, source)))
            else:
                result = copy.deepcopy(result)
        except ValueError as exc:
            if plan.get('quantity') != 'all' or not _shape_error(exc):
                raise
            children = _split_windows(source) if depth < MAX_SPLIT_DEPTH else []
            if children:
                splits[batch_id] = children
                save(p)
            else:
                run['rejections'].append(dict(stage=batch_id, reason=str(exc), coverage_failure=True))
                for row in run['coverage']:
                    if row['window_id'] in root_ids:
                        row.update(status='failed', failure=str(exc))
                run['completed_batches'].append(batch_id)
                _progress(run)
                save(p)
                return
    if children is not None:
        for i, child in enumerate(children):
            _scan_task(p, run, call, save, child, batch_id+f':split{i}', root_ids, depth+1)
    else:
        _accept_scan(p, run, result, source, batch_id)
        p['discovery_cache']['scans'][key] = copy.deepcopy(result)
        scanned = {e['id'] for w in source for e in w['events']}
        for row in run['coverage']:
            if row['window_id'] in root_ids:
                row['scanned_event_ids'] = sorted(set(row.get('scanned_event_ids', [])) | scanned)
                if row['status'] != 'failed':
                    row.update(status='partial', batch_id=batch_id)
    run['completed_batches'].append(batch_id)
    if depth == 0:
        for row in run['coverage']:
            if row['window_id'] in root_ids and row['status'] != 'failed':
                row.update(status='scanned' if any(v.get('discovery_batch','').startswith(batch_id) for v in run['candidates']) else 'no_candidate', batch_id=batch_id)
    _progress(run)
    save(p)


def _review_pending(p, run, call, save, pending):
    plan = run['plan']
    try:
        result = _call(p, plan, call, 'discovery-review', _review_prompt([x[1] for x in pending]))
        assessments = _review_result(result, [x[0]['id'] for x in pending])
    except ValueError as exc:
        if plan.get('quantity') != 'all' or not _shape_error(exc):
            raise
        if len(pending) > 1:
            # Persist the reduced future batch size before making either child
            # request, including when an interruption occurs between children.
            mid = len(pending)//2
            run['review_batch_limit'] = max(1, mid)
            save(p)
            _review_pending(p, run, call, save, pending[:mid])
            _review_pending(p, run, call, save, pending[mid:])
            return
        v = pending[0][0]
        v['review_status'] = 'review_failed'
        run['rejections'].append(dict(candidate_id=v['id'], stage='review', reason=str(exc)))
        run['review_cursor'] += 1
        _rank(run)
        save(p)
        return
    # Validate the entire response before mutating candidates or cache.
    for v, clip, key in pending:
        v.update(assessments[v['id']], review_status='reviewed')
        v['validation'] = _validation(v, p, plan)
        p['discovery_cache']['reviews'][key] = copy.deepcopy(assessments[v['id']])
        if not v['validation']['eligible']:
            run['rejections'].append(dict(candidate_id=v['id'], stage='review', reason='Independent editorial checks failed.'))
    run['review_cursor'] += len(pending)
    _rank(run)
    save(p)


def _drain_reviews(p, run, call, save):
    plan = run['plan']
    cache = p['discovery_cache']
    byid = {v['id']: v for v in run['candidates']}
    while run['review_cursor'] < len(run['review_order']):
        pending = []
        for vid in run['review_order'][run['review_cursor']:]:
            v = byid[vid]
            clip = _clip(v, p)
            content = {k: val for k, val in clip.items() if k != 'promo_id'}
            key = _hash(dict(version=VERSION, model=plan['model'], clip=content))
            cached = cache['reviews'].get(key)
            if cached is not None:
                if pending:
                    break
                v.update(copy.deepcopy(cached), review_status='reviewed')
                v['validation'] = _validation(v, p, plan)
                if not v['validation']['eligible']:
                    run['rejections'].append(dict(candidate_id=vid, stage='review', reason='Independent editorial checks failed.'))
                run['review_cursor'] += 1
                _rank(run)
                save(p)
                break
            proposed = pending + [(v, clip, key)]
            if pending and (len(proposed) > run.get('review_batch_limit', REVIEW_BATCH_SIZE) or len(_review_prompt([x[1] for x in proposed]).encode('utf-8')) > MAX_PROMPT_BYTES):
                break
            pending = proposed
        if not pending:
            continue
        _review_pending(p, run, call, save, pending)


def run_discovery(p, run, call, save, material):
    """Checkpoint each completed scan/review. Caller owns job and cancellation.

    The material callback is accepted for API compatibility; exact plan windows
    and exact clip events are used directly so camera/source events are not lost.
    Provider exceptions propagate after preserving completed stages and coverage.
    """
    plan = run['plan']
    selected = [ep for ep in p['episodes'] if ep['id'] in plan['selected_ids']]
    if _source_fingerprint(selected) != plan['source_fingerprint']:
        raise ValueError('Selected source changed; preview a new discovery plan.')
    cache = p.setdefault('discovery_cache', {})
    for key in ('scans', 'reviews', 'stitches'):
        cache.setdefault(key, {})
    run['status'] = 'running'
    _progress(run)
    run.pop('last_failure', None)
    save(p)
    try:
        windows = {w['id']: w for w in plan['windows']}
        run['phase'] = 'scan'
        for batch in plan['batches']:
            if batch['id'] in run['completed_batches']:
                continue
            source = [windows[i] for i in batch['window_ids']]
            _scan_task(p, run, call, save, source, batch['id'], batch['window_ids'])
            if plan.get('per_episode'):
                _dedup(run)
                _rank(run)
                save(p)
            elif plan.get('quantity') == 'all':
                # Review each scan chunk promptly so useful ranked cuts survive
                # an eventual quota, cancellation or transport interruption.
                run['phase'] = 'review'
                order = run.setdefault('review_order', [])
                order.extend(vid for vid in _dedup(run) if vid not in order)
                save(p)
                _drain_reviews(p, run, call, save)
                run['phase'] = 'scan'
        run['phase'] = 'stitch'
        if not run['stitching_done']:
            if plan['structure'] in ('stitched', 'either') and not plan.get('per_episode'):
                source, links = _targeted_windows(run)
                if source:
                    key = _hash(dict(version=VERSION, scope=_scope(plan), windows=source, links=links))
                    result = cache['stitches'].get(key)
                    try:
                        if result is None:
                            result = _scan_response(_call(p, plan, call, 'discovery-stitch', _prompt('discovery-stitch', plan, source, links)))
                        _accept_scan(p, run, result, source, 'targeted-stitch')
                        cache['stitches'][key] = copy.deepcopy(result)
                        run['stitching_requested'] = True
                    except ValueError as exc:
                        if plan.get('quantity') != 'all' or not _shape_error(exc):
                            raise
                        run['rejections'].append(dict(stage='targeted-stitch', reason=str(exc)))
            run['stitching_done'] = True
            save(p)
        if plan.get('quantity') == 'all':
            order = run.setdefault('review_order', [])
            order.extend(vid for vid in _dedup(run) if vid not in order)
            save(p)
        if 'review_order' not in run:
            run['review_order'] = _dedup(run)
            save(p)
        run['phase'] = 'review'
        if not plan.get('per_episode'):
            _drain_reviews(p, run, call, save)
        run['phase'] = 'rank'
        _rank(run, complete=True)
        run.update(status='done', phase='complete', completed_at=time.time())
        save(p)
    except Exception as exc:
        failure = dict(phase=run['phase'], message=str(exc), at=time.time())
        run['failures'].append(failure)
        run['last_failure'] = failure
        _progress(run)
        save(p)
        raise
    return run




def validate_repair_selection(p, run, episode_ids):
    if not run['plan'].get('per_episode'):
        raise ValueError('Episode repair is available for per-episode discovery runs.')
    if (not isinstance(episode_ids,list) or not episode_ids or any(not isinstance(eid,str) for eid in episode_ids)
            or len(episode_ids)!=len(set(episode_ids)) or not set(episode_ids)<=set(run['plan']['selected_ids'])):
        raise ValueError('Select distinct episodes included in this discovery run.')
    view = project_episode_results(p,run)
    byid = {row['episode_id']:row for row in view['episode_results']}
    if any(byid[eid].get('draft',{}).get('validation',{}).get('eligible') for eid in episode_ids):
        raise ValueError('Repair only episodes whose current draft fails local checks, or has no draft.')
    return view


def _clear_editorial(v):
    # Shared policy: any new/changed or explicitly rechecked cut loses stale AI
    # ratings. Revalidating a saved projection alone preserves historical scores.
    from workflow import clear_review
    clear_review(v)
    return v


def repair_episode(p, run, episode_id, call, save, material):
    """Save one episode-only proposal. Never replace or review the current cut."""
    work = validate_repair_selection(p,run,[episode_id])
    scoped = [w for w in work['plan']['windows'] if w['episode_id']==episode_id]
    if not scoped:
        raise ValueError('No exact source window is available for this episode.')
    current_events = {e['id']:e for ep in p['episodes'] if ep['id']==episode_id for e in ep['events']}
    if any(current_events.get(e['id'])!=e for w in scoped for e in w['events']):
        raise ValueError('Episode source changed; start a new discovery plan before repairing.')
    previous = next(row for row in work['episode_results'] if row['episode_id']==episode_id)
    prior = previous.get('draft')
    def overlap(window):
        segments = prior.get('segments',[]) if prior else []
        return sum(max(0,min(s['end'],window['end'])-max(s['start'],window['start']))
                   for s in segments if isinstance(s,dict) and s.get('episode_id')==episode_id
                   and _number(s.get('start')) and _number(s.get('end')))
    source = [max(scoped,key=overlap)]
    failed = [copy.deepcopy(c) for c in prior.get('validation',{}).get('checks',[]) if not c.get('passed')] if prior else []
    context = dict(episode_id=episode_id, failed_local_checks=failed,
                   previous_ranges=prior.get('segments',[]) if prior else [],
                   previous_question=prior.get('question','') if prior else '')
    prompt = ('Propose a repair for this episode using ONLY the exact source window below. Address the failed LOCAL cut checks. '
              'Return JSON {candidates:[draft],opportunities:[]}, with at most ONE candidate. Include concise reason (what changed) '
              'and weakness (tradeoff) fields. Return none only if no usable timestamped source cut exists. '
              'Never invent dialogue, timestamps or a bridge. This is a proposal for the user to compare; it will not be applied automatically. '
              + DRAFT + ' SETTINGS:' + _json(_scope(work['plan'])) + ' REPAIR CONTEXT:' + _json(context)
              + ' EXACT SOURCE WINDOWS:' + _json(source))
    attempt = dict(id=uuid.uuid4().hex,episode_id=episode_id,at=time.time(),status='running',
                   previous_candidate_id=prior.get('id') if prior else None,source_window_ids=[w['id'] for w in source])
    variation = None
    try:
        result = _scan_response(_call(p,work['plan'],call,'discovery-episode-repair',prompt))
        if len(result['candidates'])>1:
            raise ValueError('Episode repair returned an invalid or oversized response: expected at most one candidate.')
        before = len(work.get('rejected_candidates',[]))
        accepted = _accept_scan(p,work,result,source,'episode-repair:'+attempt['id'])
        rejected = work.get('rejected_candidates',[])[before:]
        variation = (accepted or rejected or [None])[0]
        if variation:
            _clear_editorial(variation)
            variation['validation'] = _validation(variation,p,work['plan'])
            variation['review_status'] = 'unreviewed' if variation['validation']['eligible'] else 'needs_repair'
            attempt.update(status='proposal_ready',candidate_id=variation['id'],message='Repair proposal saved. Choose Keep new cut or Keep original.')
        else:
            attempt.update(status='no_candidate',message='No usable repair draft was returned. The original is unchanged.')
    except Exception as exc:
        attempt.update(status='unavailable' if _shape_error(exc) else 'error',message=str(exc))
        if not _shape_error(exc):
            run.setdefault('repair_attempts',[]).append(attempt)
            save(p)
            raise
    proposal = dict(id=attempt['id'],episode_id=episode_id,variation=copy.deepcopy(variation),
                    previous_candidate_id=attempt['previous_candidate_id'],at=attempt['at'],
                    change=(variation.get('reason') or variation.get('distinction') or 'Source ranges proposed for the failed local checks.') if variation else '',
                    tradeoff=(variation.get('weakness') or 'Compare the new source ranges before keeping this cut.') if variation else '',
                    eligible=bool(variation and variation['validation']['eligible']),status=attempt['status'],message=attempt['message'])
    # Keep proposals outside candidates/shortlist until the explicit decision.
    existing = run.get('repair_proposals',{}).get(episode_id)
    if existing:
        run.setdefault('repair_proposal_history',[]).append(copy.deepcopy(existing))
    run.setdefault('repair_proposals',{})[episode_id] = proposal
    run.setdefault('repair_attempts',[]).append(attempt)
    _rank(run,complete=bool(run.get('summary',{}).get('complete') or run.get('status')=='done'))
    save(p)
    return run


def repair_episodes(p, run, episode_ids, call, save, material):
    """Sequential episode-local proposals; each completed result is checkpointed."""
    validate_repair_selection(p,run,episode_ids)
    for episode_id in episode_ids:
        repair_episode(p,run,episode_id,call,save,material)
    return run


def decide_episode_repair(p, run, episode_id, keep_new, save):
    if type(keep_new) is not bool:
        raise ValueError('Choose whether to keep the new cut.')
    proposal = run.get('repair_proposals',{}).get(episode_id)
    if not proposal:
        raise ValueError('No repair proposal is waiting for this episode.')
    view = project_episode_results(p,run)
    row = next((r for r in view['episode_results'] if r['episode_id']==episode_id),None)
    original = row.get('draft') if row else None
    if (original.get('id') if original else None)!=proposal.get('previous_candidate_id'):
        raise ValueError('The current cut changed after this proposal. Request a new repair.')
    if keep_new:
        variation = copy.deepcopy(proposal.get('variation'))
        if not isinstance(variation,dict):
            raise ValueError('This repair did not return an actual draft to keep.')
        _clear_editorial(variation)
        variation['validation'] = _validation(variation,p,run['plan'])
        variation['review_status'] = 'unreviewed' if variation['validation']['eligible'] else 'needs_repair'
        variation['repair_of'] = proposal.get('previous_candidate_id')
        run['candidates'].append(variation)
        run.setdefault('episode_choices',{})[episode_id] = variation['id']
    elif original:
        run.setdefault('episode_choices',{})[episode_id] = original['id']
        for variation in run['candidates'] + run.get('rejected_candidates',[]):
            if variation['id']==original['id']:
                _clear_editorial(variation)
                variation['validation'] = _validation(variation,p,run['plan'])
                variation['review_status'] = 'unreviewed' if variation['validation']['eligible'] else 'needs_repair'
    run.setdefault('repair_decisions',[]).append(dict(episode_id=episode_id,keep_new=keep_new,at=time.time(),proposal=copy.deepcopy(proposal)))
    del run['repair_proposals'][episode_id]
    _rank(run,complete=bool(run.get('summary',{}).get('complete') or run.get('status')=='done'))
    save(p)
    return run


def review_episode(p, run, episode_id, call, save, material):
    """Optional paid virality rating of the chosen cut, preserving local warnings."""
    view = project_episode_results(p,run)
    row = next((r for r in view.get('episode_results',[]) if r['episode_id']==episode_id),None)
    v = row.get('draft') if row else None
    if not v:
        raise ValueError('There is no saved draft to review for this episode.')
    result = _call(p,run['plan'],call,'discovery-episode-review','Local cut-check warnings (retain these failures; do not invent missing source): '+json.dumps([c for c in v['validation']['checks'] if not c['passed']],ensure_ascii=False)+' '+_review_prompt([_clip(v,p)]))
    assessment = _review_result(result,[v['id']])[v['id']]
    for current in run['candidates'] + run.get('rejected_candidates',[]):
        if current['id']==v['id']:
            current.update(assessment,review_status='reviewed')
            current['validation'] = _validation(current,p,run['plan'])
            break
    _rank(run,complete=bool(run.get('summary',{}).get('complete') or run.get('status')=='done'))
    save(p)
    return run
