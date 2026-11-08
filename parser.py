"""Lossless timestamp extraction and conservative source-cut validation."""
import hashlib
import math
import re

STAMP = r'\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?'
WEIGHTS = {'Hook':25,'Standalone clarity':25,'Escalation':15,'Cliffhanger':25,'Cut quality':10}

def seconds(value):
    parts = [float(p) for p in value.split(':')]
    if any(p >= 60 for p in parts[1:]): raise ValueError('Invalid timestamp: '+value)
    return sum(p * 60**i for i,p in enumerate(reversed(parts)))

def parse_episode(filename, text):
    if not filename.lower().endswith('.txt'): raise ValueError('Only TXT screenplays are supported.')
    eid = hashlib.sha256((filename+'\0'+text).encode()).hexdigest()[:12]
    pattern = re.compile(r'\*\*([^\n]+?):\*\*\s*\n\s*Start:\s*\(('+STAMP+r')\)\s*\|\s*End:\s*\(('+STAMP+r')\)\s*\n(.*?)(?=\n\s*\*\*[^\n]+?:\*\*|\n\s*SCENE\s+\d|\n\s*---|\Z)', re.I|re.S)
    found = [(m[1],m[2],m[3],m[4].strip()) for m in pattern.finditer(text)]
    headers=re.findall(r'^\s*\*\*[^\n]+?:\*\*\s*$',text,re.M)
    if headers and len(headers)!=len(found):raise ValueError(filename+': A screenplay block has missing or malformed timestamps. Correct it before importing; no dialogue may be omitted.')
    if '\ufffd' in text:raise ValueError(filename+': Unreadable characters found. Save this TXT as UTF-8 and import again.')
    if not found:
        simple = re.compile(r'^\s*\[?('+STAMP+r')\s*(?:-->|–|-)\s*('+STAMP+r')\]?\s*([^:\n]+):\s*(.*?)(?=\n\s*\[?'+STAMP+r'|\Z)',re.M|re.S)
        found = [(m[3],m[1],m[2],m[4].strip()) for m in simple.finditer(text)]
    if not found: raise ValueError(filename+': No supported timestamped turns found. Use **SPEAKER:** then Start: (MM:SS.ss) | End: (MM:SS.ss), or [MM:SS-MM:SS] SPEAKER: text.')
    events=[]; warnings=[]
    for n,(speaker,start,end,body) in enumerate(found):
        a,b=seconds(start),seconds(end)
        if b<=a or not body: raise ValueError(filename+': Empty or reversed timestamp block '+str(n+1))
        upper=speaker.upper()
        kind= 'camera' if upper.startswith('CAMERA') else 'action' if upper.startswith('ACTION') else 'sound' if upper.startswith(('SOUND','SFX','MUSIC')) else 'dialogue'
        events.append(dict(id=f'{eid}:{n+1:04}',start=a,end=b,speaker=speaker,kind=kind,text=body))
        if kind=='dialogue' and len(body.split())/(b-a)>4: warnings.append('Fast scripted speech at '+start+'; verify against audio.')
    number=re.search(r'EPISODE NUMBER:\s*(?:Episode\s*)?(\d+)',text,re.I)
    number=int(number[1]) if number else int(re.search(r'\d+',filename)[0]) if re.search(r'\d+',filename) else 0
    logline=re.search(r'EPISODE LOGLINE:\s*([^\n]+)',text,re.I)
    return dict(id=eid,number=number,filename=filename,title=f'Episode {number}',logline=logline[1] if logline else '',duration=max(e['end'] for e in events),events=events,warnings=list(dict.fromkeys(warnings)))

def validate_cut(v,episodes,selected_ids,minimum=20,maximum=60,structure='continuous'):
    checks=[]; warnings=['Script timestamps are estimates; footage and audio sync are unverified.']; evidence=[]
    def check(name,passed,detail): checks.append(dict(name=name,passed=bool(passed),detail=detail))
    byid={e['id']:e for e in episodes}; segs=v.get('segments',[]); valid=[]; duration=0
    source_ok=isinstance(segs,list) and 0<len(segs)<=8
    for s in segs if isinstance(segs,list) else []:
        if not isinstance(s,dict): source_ok=False; continue
        ep=byid.get(s.get('episode_id')) if isinstance(s.get('episode_id'),str) else None; a=s.get('start'); b=s.get('end')
        ok=ep and ep['id'] in selected_ids and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in (a,b)) and 0<=a<b<=ep['duration']+.001
        if not ok: source_ok=False; continue
        valid.append((ep,a,b));duration+=b-a
    check('Source selection',source_ok,'All segments must exist inside selected episodes.')
    check('Cut structure',len(valid)==1 if structure=='continuous' else len(valid)>=1,'Continuous uses one uninterrupted source range; stitched uses ordered ranges.')
    check('20–60 second duration',minimum-.001<=duration<=maximum+.001 and minimum>=20 and maximum<=60,f'{duration:.2f}s; requested {minimum}–{maximum}s.')
    boundary=True
    for ep,a,b in valid:
        turns=[e for e in ep['events'] if e['kind']=='dialogue']
        for e in turns:
            if e['start']+.001<a<e['end']-.001 or e['start']+.001<b<e['end']-.001: boundary=False
    check('No dialogue cut mid-line',boundary and bool(valid),'No segment starts or ends inside a single timestamped dialogue block. Adjacent blocks by the same speaker may be separated.')
    contained={e['id']:e for ep,a,b in valid for e in ep['events'] if e['start']>=a-.001 and e['end']<=b+.001}
    beats=v.get('beats',[]); roles=['Hook','Context','Escalation','Cliffhanger']; proof=True; offsets=[]
    for role in roles:
        beat=next((x for x in beats if isinstance(x,dict) and x.get('role')==role),{}) if isinstance(beats,list) else {}
        ids=beat.get('event_ids',[]); ev=[contained[i] for i in ids if isinstance(i,str) and i in contained] if isinstance(ids,list) else []
        proof &= bool(ev) and len(ev)==len(ids)
        evidence.append(dict(role=role,reason=beat.get('reason',''),events=ev))
        positions=[]; offset=0
        for ep,a,b in valid:
            positions += [offset+e['start']-a for e in ev if e in ep['events'] and a-.001<=e['start'] and e['end']<=b+.001]
            offset+=b-a
        offsets.append(min(positions) if positions else -1)
    check('Hook in first 3 seconds',0<=offsets[0]<=3,'First hook evidence must begin within three seconds.')
    narrative=v.get('narrative',{})
    rubric=v.get('rubric',[]); values={r.get('criterion'):r.get('score') for r in rubric if isinstance(r,dict)} if isinstance(rubric,list) else {}
    score_ok=all(isinstance(values.get(k),(int,float)) and not isinstance(values[k],bool) and math.isfinite(values[k]) and 0<=values[k]<=5 for k in WEIGHTS)
    for c in checks:c['assessed']=True
    eligible=all(c['passed'] for c in checks)
    rubric_score=round(sum(values[k]*w/5 for k,w in WEIGHTS.items())) if score_ok and v.get('review_status')!='pending' else None
    editorial_passed=bool(score_ok and all(narrative.get(k) is True for k in ('standalone','escalation','cliffhanger','coherent'))) if rubric_score is not None else None
    return dict(editorial_passed=editorial_passed,rubric_score=rubric_score,eligible=eligible,duration=round(duration,2),checks=checks,warnings=warnings,evidence=evidence,score=round(sum(values[k]*w/5 for k,w in WEIGHTS.items())) if eligible and rubric_score is not None else None)
