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
from prompt_payload import SOURCE_FORMAT, payload_json
from parser import WEIGHTS, validate_cut
from workflow import DRAFT, normalize_sources

VERSION = 4
MAX_PROMPT_BYTES = 64000
WINDOW_BYTES = 20000
BATCH_SOURCE_BYTES = 48000
MAX_WINDOWS_PER_BATCH = 5
MAX_RESPONSE_BYTES = 24000
CANDIDATES_PER_BATCH = 3
OPPORTUNITIES_PER_BATCH = 8
REVIEW_BATCH_SIZE = 5
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
    ids = body.get('selected_ids')
    known = {ep['id'] for ep in p['episodes']}
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids)) or not set(ids) <= known):
        raise ValueError('Select valid, distinct source episodes.')
    lo, hi = body.get('minimum', 20), body.get('maximum', 60)
    if not all(_number(x) for x in (lo, hi)) or not 20 <= lo <= hi <= 60:
        raise ValueError('Use a finite 20–60 second duration range.')
    structure = body.get('structure', 'continuous')
    quantity = body.get('quantity', 'auto')
    limit = body.get('limit', 30)
    if structure not in ('continuous', 'stitched', 'either'):
        raise ValueError('Choose continuous, stitched or either structure.')
    if quantity not in ('auto', 'cap') or type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError('Choose automatic quantity or an upper cap of 1–30 promos.')
    emphasis = body.get('emphasis', '')
    if not isinstance(emphasis, str) or len(emphasis) > 500:
        raise ValueError('Keep the discovery direction within 500 characters.')
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
        if current and (len(proposed) > MAX_WINDOWS_PER_BATCH
                        or _bytes([lookup[i] for i in proposed]) > BATCH_SOURCE_BYTES):
            batches.append(dict(id=f'batch-{len(batches)+1}', window_ids=current))
            current = []
        current.append(window['id'])
    if current:
        batches.append(dict(id=f'batch-{len(batches)+1}', window_ids=current))
    stitch = int(structure in ('stitched', 'either'))
    candidate_bound = (len(batches) + stitch) * CANDIDATES_PER_BATCH
    # A large clip may occupy a review request by itself, so ceil(n/5) is
    # not a safe upper bound. No repair/retry loop is performed here.
    maximum = len(batches) + stitch + candidate_bound
    return dict(schema_version=VERSION, selected_ids=list(ids), structure=structure,
                quantity=quantity, limit=limit, minimum=float(lo), maximum=float(hi),
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
    return dict(id=uuid.uuid4().hex, status='pending', phase='scan',
                created_at=time.time(), plan=copy.deepcopy(plan),
                coverage=[dict(window_id=w['id'], episode_id=w['episode_id'], status='pending') for w in plan['windows']],
                completed_batches=[], candidates=[], opportunities=[], shortlist=[],
                rejections=[], review_cursor=0, failures=[], stitching_done=False)


def _scope(plan):
    return {k: plan[k] for k in ('model', 'minimum', 'maximum', 'structure', 'emphasis')}


def _prompt(stage, plan, windows, opportunities=None):
    request = ('Scan EVERY supplied source window for supported standalone promo opportunities, '
               'and draft the strongest candidates in this same response. An empty list is valid; '
               'never fill a quota. Return JSON {candidates:[draft],opportunities:[{key,reason,event_ids:[exact source IDs]}]}. '
               f'Return at most {CANDIDATES_PER_BATCH} candidates and {OPPORTUNITIES_PER_BATCH} opportunities; '
               f'keep the complete JSON under {MAX_RESPONSE_BYTES} UTF-8 bytes. '
               'Supplied windows may overlap; do not duplicate the same cut. '
               'Only source IDs and complete dialogue turns supplied below are allowed. '
               'Any rough_priority is scheduling advice, never an approved editorial score. ')
    if stage == 'discovery-stitch':
        request = ('Make at most ONE targeted stitching attempt using only the exact source windows below, '
                   'retrieved from linked opportunities. Return no candidates unless the included dialogue '
                   'supports one coherent standalone conflict with understandable transitions. '
                   'Do not invent a bridge or use unrelated plots. ' + request)
    prompt = request + DRAFT.replace('Produce exactly ONE draft, never alternatives or variations.',
                                    f'Produce up to {CANDIDATES_PER_BATCH} distinct drafts; return none if unsupported.')
    prompt += ' SETTINGS:' + _json(_scope(plan))
    if opportunities:
        prompt += ' LINKED OPPORTUNITIES:' + _json(opportunities)
    return prompt + SOURCE_FORMAT + ' EXACT SOURCE WINDOWS:' + payload_json(windows)


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
    return validate_cut(v, p['episodes'], plan['selected_ids'], plan['minimum'], plan['maximum'], structure)


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
            'Do not require a complete emotional arc: select an understandable excerpt with minimal setup, rising tension and an unresolved turn. Stop before the payoff; never extend beyond the duration limit to complete a scene. '
            'The question is an editorial claim to test, not information given to a viewer. '
            'Assess every clip independently; another clip cannot supply missing context or clarity. '
            'Use the exact playback order. Do not infer unseen context or future episodes. '
            'Return JSON {results:[{promo_id,narrative:{standalone:boolean,escalation:boolean,cliffhanger:boolean,coherent:boolean},'
            'rubric:[{criterion,score,reason}],review_notes:string}]}. Return exactly one result per promo_id; '
            'five unique criteria: Hook, Standalone clarity, Escalation, Cliffhanger, Cut quality; '
            'scores finite 0–5 and source-based reasons required.' + SOURCE_FORMAT + ' CLIPS:' + payload_json(clips))


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
        normalize_sources(v, p['episodes'], plan['selected_ids'])
        if any(not isinstance(v.get(k), str) or not v[k].strip() for k in ('angle', 'distinction', 'question')):
            reason = 'Candidate is missing its angle, intent or unanswered question.'
        validation = _validation(v, p, plan)
        failures = [c['name']+': '+c['detail'] for c in validation['checks'] if not c['passed'] and c['name'] not in EDITORIAL_CHECKS]
        if failures:
            reason = '; '.join(failures)
        if not reason:
            segs = v['segments']
            signatures = [(s['episode_id'], s['start'], s['end']) for s in segs]
            if len(signatures) != len(set(signatures)):
                reason = 'Repeated identical source segments are not a distinct story.'
            elif any(s['episode_id'] not in episodes for s in segs):
                reason = 'Candidate uses an episode outside this source window batch.'
            else:
                byid = {ep['id']: ep for ep in p['episodes']}
                touched = {e['id'] for s in segs for e in byid[s['episode_id']]['events']
                           if e['start'] < s['end'] and e['end'] > s['start']}
                if not touched <= supplied:
                    reason = 'Candidate uses source events not supplied in its exact windows.'
        if not reason and len(_review_prompt([_clip(v, p)]).encode('utf-8')) > MAX_PROMPT_BYTES:
            reason = 'Candidate clip is too large for bounded independent review.'
        if reason:
            v.update(review_status='rejected', validation=validation)
            run['rejections'].append(dict(candidate_id=v['id'], stage=batch_id, reason=reason))
        else:
            v['validation'] = validation  # Editorial checks remain false/pending.
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
    for v in sorted(run['candidates'], key=lambda v: -_priority(v)):
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
    pairs = [(a, b) for i, a in enumerate(opportunities) for b in opportunities[i+1:]
             if a['batch_id'] != b['batch_id'] and (a['key'].casefold() == b['key'].casefold()
                                                   or set(a['event_ids']) & set(b['event_ids']))]
    if not pairs:
        return [], []
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
    eligible = [v for v in run['candidates'] if v.get('review_status') == 'reviewed' and v.get('validation', {}).get('eligible') and v.get('validation',{}).get('editorial_passed')]
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
    for v in sorted(remaining, key=quality, reverse=True):
        if any(_same_playback_order(v, other) and overlap >= .85 and ratio >= .65
               for other in distinct for overlap, ratio in [_overlap(v, other)]):
            run['ranking_exclusions'].append(dict(candidate_id=v['id'], reason='High-overlap source cut suppressed after independent review in favor of the stronger eligible cut.'))
        else:
            distinct.append(v)
    remaining = distinct
    selected = []
    adjustments = {}
    limit = run['plan']['limit']
    while remaining and len(selected) < limit:
        def rank(v):
            source = {s['episode_id'] for s in v['segments']}
            repeated = sum(bool(source & {s['episode_id'] for s in other['segments']}) for other in selected)
            overlap = max((_overlap(v, other)[0] for other in selected), default=0)
            clarity = next(r['score'] for r in v['rubric'] if r['criterion'] == 'Standalone clarity')
            return (v['validation']['score'] - repeated*3 - overlap*15, clarity, v['validation']['score'])
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
                             posting_reason=posting_reason(v))
                        for i, v in enumerate(selected)]
    run['summary'] = dict(scanned_windows=sum(c['status'] != 'pending' for c in run['coverage']),
                          total_windows=len(run['coverage']), candidates=len(run['candidates']),
                          reviewed=sum(v.get('review_status') == 'reviewed' for v in run['candidates']),
                          eligible=len([v for v in run['candidates'] if v.get('validation', {}).get('eligible')]),
                          shortlisted=len(selected), complete=bool(complete), shortlist_policy=SHORTLIST_POLICY,
                          limitation=run['plan']['coverage_limitation'])


def run_discovery(p, run, call, save, material, review_enabled=True):
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
    run.pop('last_failure', None)
    save(p)
    try:
        windows = {w['id']: w for w in plan['windows']}
        run['phase'] = 'scan'
        for batch in plan['batches']:
            if batch['id'] in run['completed_batches']:
                continue
            source = [windows[i] for i in batch['window_ids']]
            key = _hash(dict(version=VERSION, scope=_scope(plan), windows=source))
            result = cache['scans'].get(key)
            if result is None:
                result = _scan_response(_call(p, plan, call, 'discovery-scan', _prompt('discovery-scan', plan, source)))
            else:
                result = copy.deepcopy(result)
            _accept_scan(p, run, result, source, batch['id'])
            cache['scans'][key] = copy.deepcopy(result)
            for coverage in run['coverage']:
                if coverage['window_id'] in batch['window_ids']:
                    coverage.update(status='scanned' if result['candidates'] else 'no_candidate', batch_id=batch['id'])
            run['completed_batches'].append(batch['id'])
            save(p)
        run['phase'] = 'stitch'
        if not run['stitching_done']:
            if plan['structure'] in ('stitched', 'either'):
                source, links = _targeted_windows(run)
                if source:
                    key = _hash(dict(version=VERSION, scope=_scope(plan), windows=source, links=links))
                    result = cache['stitches'].get(key)
                    if result is None:
                        result = _scan_response(_call(p, plan, call, 'discovery-stitch', _prompt('discovery-stitch', plan, source, links)))
                    _accept_scan(p, run, result, source, 'targeted-stitch')
                    cache['stitches'][key] = copy.deepcopy(result)
                    run['stitching_requested'] = True
            run['stitching_done'] = True
            save(p)
        if 'review_order' not in run:
            run['review_order'] = _dedup(run)
            save(p)
        if not review_enabled:
            for v in run['candidates']:
                v['validation']=_validation(v,p,plan)
                if v.get('review_status')!='reviewed':v['review_status']='unreviewed'
            run['shortlist']=[dict(id=v['id'],title=v['angle'],opportunity=v.get('reason',v.get('distinction','')),source_ids=list(dict.fromkeys(s['episode_id'] for s in v['segments'])),variations=[copy.deepcopy(v)]) for v in run['candidates'] if v['id'] in run['review_order']]
            run.update(status='done',phase='awaiting_review',completed_at=time.time())
            run['summary']=dict(candidates=len(run['candidates']),reviewed=sum(v.get('review_status')=='reviewed' for v in run['candidates']),eligible=len(run['shortlist']),shortlisted=len(run['shortlist']),complete=True,shortlist_policy='Unranked source-valid candidates. Run Virality Review to score and shortlist.')
            save(p)
            return run
        run['phase'] = 'review' 
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
                if pending and (len(proposed) > REVIEW_BATCH_SIZE or len(_review_prompt([x[1] for x in proposed]).encode('utf-8')) > MAX_PROMPT_BYTES):
                    break
                pending = proposed
            if not pending:
                continue
            result = _call(p, plan, call, 'discovery-review', _review_prompt([x[1] for x in pending]))
            assessments = _review_result(result, [x[0]['id'] for x in pending])
            # Validate the entire batch before mutating any candidate or cache.
            for v, clip, key in pending:
                v.update(assessments[v['id']], review_status='reviewed')
                v['validation'] = _validation(v, p, plan)
                cache['reviews'][key] = copy.deepcopy(assessments[v['id']])
                if not v['validation']['eligible']:
                    run['rejections'].append(dict(candidate_id=v['id'], stage='review', reason='Independent editorial checks failed.'))
            run['review_cursor'] += len(pending)
            _rank(run)
            save(p)
        run['phase'] = 'rank'
        _rank(run, complete=True)
        run.update(status='done', phase='complete', completed_at=time.time())
        save(p)
    except Exception as exc:
        failure = dict(phase=run['phase'], message=str(exc), at=time.time())
        run['failures'].append(failure)
        run['last_failure'] = failure
        save(p)
        raise
    return run
