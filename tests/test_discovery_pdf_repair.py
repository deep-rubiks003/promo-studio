import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import copy,json
import fitz
import server
from test_new_workflow import studio,finish
from test_discovery import plan, new_run,run_discovery,response,review_inputs

from test_rejected_repair import draft as episode_draft

def draft(ep):
    v=episode_draft(ep);v.update(distinction='One conflict',reason='Exact source',weakness='Timing')
    return v

def seeded(p):
    run=new_run(plan(p,quantity='all',per_episode=True))
    def fake(pid,stage,prompt):
        if stage=='discovery-scan':
            source=json.loads(prompt.split('EXACT SOURCE WINDOWS:',1)[1])[0]
            ep=next(ep for ep in p['episodes'] if ep['id']==source['episode_id'])
            v=draft(ep)
            if ep['id']==p['episodes'][1]['id']:v['segments'][0]['end']=90
            return dict(candidates=[v],opportunities=[])
        clips=review_inputs(prompt);result=response(clips)
        if clips[0]['segments'][0]['episode_id']==p['episodes'][1]['id']:
            result['results'][0]['narrative']['coherent']=False
            result['results'][0]['review_notes']='Two scenes do not connect.'
        return result
    p['discovery_runs']=[run];run_discovery(p,run,fake,server.save,server.material);server.save(p)
    return run

def test_discovery_pdf_and_failed_results_are_available_without_key(studio,monkeypatch):
    p,c,h=studio;run=seeded(p);before=copy.deepcopy(server.load(p['id']))
    monkeypatch.setitem(server.state,'key','')
    monkeypatch.setattr(server,'call',lambda *args:(_ for _ in ()).throw(AssertionError('Viewing/export uses no AI')))
    viewed=c.get('/api/projects/'+p['id']+'/discovery/'+run['id']+'/view').json()
    assert len(viewed['groups'])==3 and any(not g['eligible'] for g in viewed['groups'])
    r=c.get('/api/projects/'+p['id']+'/discovery/'+run['id']+'/export.pdf');assert r.status_code==200 and r.headers['content-type']=='application/pdf'
    pdf=fitz.open(stream=r.content,filetype='pdf');text=' '.join(p.get_text() for p in pdf)
    assert 'Posting priority' in text.upper().title() or 'POSTING PRIORITY' in text
    assert 'Needs changes' in text and 'Episode accounting' in text
    assert server.load(p['id'])==before

def test_episode_repair_api_operation_is_wired(studio,monkeypatch):
    p,c,h=studio;run=seeded(p);calls=[]
    def fake(pid,stage,prompt):
        calls.append(stage)
        if stage=='discovery-episode-repair':return dict(candidates=[draft(p['episodes'][1])],opportunities=[])
        assert stage=='discovery-episode-repair-review'
        return response(review_inputs(prompt))
    monkeypatch.setattr(server,'call',fake)
    jid=c.post('/api/projects/'+p['id']+'/discovery/'+run['id']+'/episodes/'+p['episodes'][1]['id']+'/repair',headers=h,json={}).json()['job_id']
    result=finish(c,jid);assert result['status']=='done',result
    assert calls==['discovery-episode-repair']
    saved=server.load(p['id'])['discovery_runs'][-1]
    assert saved['repair_proposals'][p['episodes'][1]['id']]['variation']['validation']['eligible']
    current=next(row for row in saved['episode_results'] if row['episode_id']==p['episodes'][1]['id'])
    assert not current['draft']['validation']['eligible']
