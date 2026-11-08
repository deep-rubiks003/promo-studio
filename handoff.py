"""Printable, escaped source-cut instructions; does not generate or alter dialogue."""
from html import escape
import math
from parser import validate_cut

def tc(s):return f'{int(s//60):02}:{s%60:05.2f}'
def e(value):return escape(str(value if value is not None else ''))
def sheet(p):
    parts=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Editor handoff</title><style>body{font:15px/1.6 Arial,sans-serif;max-width:960px;margin:40px auto;padding:0 24px;color:#202020}h1,h2,h3{line-height:1.3}article{break-before:page;margin:40px 0}table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;border:1px solid #bbb;padding:9px}blockquote{white-space:pre-wrap;margin:8px 0 20px;padding:12px;background:#f4f4f4}.note{color:#555}li{margin:7px 0}@media print{body{margin:0;max-width:none}.print{display:none}article:first-of-type{break-before:auto}}</style><body>',f'<h1>{e(p["name"])} — Editor handoff</h1><p class="note">Source cut instructions. Script timestamps require footage/audio verification. Editorial scores are not viral probabilities. Unchecked spoilers are not cleared.</p>']
    episodes={ep['id']:ep for ep in p['episodes']};brief=p.get('brief') or {};count=0
    for g in p.get('groups',[]):
        for v in g['variations']:
            count+=1;valid=validate_cut(v,p['episodes'],g.get('source_ids',brief.get('selected_ids',[])),brief.get('minimum',20),brief.get('maximum',60),brief.get('structure','continuous'))
            parts+=[f'<article><h2>Promo {count}: {e(v.get("angle",g["title"]))}</h2>',f'<p><strong>{"Eligible" if valid["eligible"] else "Not eligible — review required"}</strong> · {valid["duration"]} seconds · {e(brief.get("structure"))}</p>',f'<p><strong>Intent:</strong> {e(v.get("distinction"))}</p><p><strong>Posting reason:</strong> {e(v.get("reason"))}</p><h3>Ordered source cuts and original dialogue</h3>']
            offset=0
            for i,s in enumerate(v.get('segments',[]),1):
                ep=episodes.get(s.get('episode_id'));a=s.get('start');b=s.get('end')
                if not ep or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (a,b)) or not 0<=a<b<=ep['duration']:continue
                parts.append(f'<h4>Cut {i} · Episode {e(ep["number"])} · source {tc(a)}–{tc(b)} · promo {tc(offset)}–{tc(offset+b-a)}</h4>')
                turns=[x for x in ep['events'] if x['kind']=='dialogue' and a<=x['start'] and x['end']<=b]
                if not turns:parts.append('<p>No complete dialogue turns in this segment; inspect source action.</p>')
                for turn in turns:parts.append(f'<p><strong>{e(turn["speaker"])}</strong> · {tc(turn["start"])}–{tc(turn["end"])}</p><blockquote>{e(turn["text"])}</blockquote>')
                offset+=b-a
            parts.append('<h3>Story beats</h3><ul>')
            for beat in valid['evidence']:
                refs=[]
                for event in beat['events']:
                    ep=next((ep for ep in p['episodes'] if any(x['id']==event['id'] for x in ep['events'])),None)
                    refs.append(f'Episode {ep["number"] if ep else "?"} {tc(event["start"])}–{tc(event["end"])}')
                parts.append(f'<li><strong>{e(beat["role"])}</strong>: {e(beat["reason"])}<br>{e("; ".join(refs))}</li>')
            parts+=[f'</ul><p><strong>Unanswered question:</strong> {e(v.get("question"))}</p><h3>Checks and editorial review</h3><ul>']
            for check in valid['checks']:parts.append(f'<li>{"Pass" if check["passed"] else "Fail"}: {e(check["name"])} — {e(check["detail"])}</li>')
            parts.append('</ul>')
            for r in v.get('rubric') or []:parts.append(f'<p>{e(r.get("criterion"))}: {e(r.get("score"))}/5 — {e(r.get("reason"))}</p>')
            parts.append(f'<p><strong>Editor concern:</strong> {e(v.get("weakness"))} {e(v.get("review_notes"))}</p><h3>Spoiler checker</h3>')
            spoiler=v.get('spoiler_check')
            if not spoiler:parts.append('<p>Not checked.</p>')
            else:
                parts.append(f'<p>{e(spoiler.get("status"))}: {e(spoiler.get("summary"))}</p>')
                for check in spoiler.get('checks',[]):parts.append(f'<p><strong>{e(check["category"])}</strong>: {e(check["status"])} — {e(check["reason"])}</p>')
                for f in spoiler.get('findings',[]):
                    parts.append(f'<p><strong>{e(f.get("category",f.get("kind")))}</strong>: {e(f.get("explanation"))}<br>Suggestion: {e(f.get("suggestion"))}</p>')
                    for ev in f.get('events',[]):parts.append(f'<blockquote>Episode {e(ev.get("episode_number","?"))} · {tc(ev["start"])}–{tc(ev["end"])} · {e(ev["speaker"])}\n{e(ev["text"])}</blockquote>')
                parts.append(f'<p class="note">{e(spoiler.get("limitation"))}</p>')
            parts.append('</article>')
    if not count:parts.append('<p>No completed promo drafts to hand off.</p>')
    return ''.join(parts)+'</body></html>'
