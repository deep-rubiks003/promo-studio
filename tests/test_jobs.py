"""No-network tests for durable recovery and actual worker termination."""
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_runtime import JobStore, DurableJob, Runtime, JobCancelled, isolated_generate
from fastapi.testclient import TestClient
from test_core import SCRIPT


def blocking_provider(pipe):
    pipe.recv()
    time.sleep(30)
    pipe.send(('ok', {'should_not_complete': True}))


def test_restart_marks_only_unfinished_jobs_interrupted(tmp_path):
    path = tmp_path/'jobs.sqlite3'
    def connect():
        c = sqlite3.connect(path); c.row_factory = sqlite3.Row
        return c
    store = JobStore(connect)
    waiting = DurableJob(store, id='waiting',project_id='p',operation='generate',body={'selected_ids':['episode']},status='queued',created_at=1,stages=[])
    working = DurableJob(store, id='working',project_id='p',operation='generate',body={},status='running',created_at=2,stages=[{'name':'review','status':'running'}])
    DurableJob(store,id='done',project_id='p',operation='analyze',body={},status='done',created_at=0,stages=[])
    restarted = JobStore(connect); restarted.initialize(recover=True)
    assert restarted.get('waiting')['status'] == 'interrupted'
    assert restarted.get('waiting')['body'] == waiting['body']
    assert restarted.get('working')['stages'][0]['status'] == 'interrupted'
    assert restarted.get('done')['status'] == 'done'


def test_cancel_terminates_real_spawned_process():
    runtime = Runtime(); errors=[]
    def run():
        try:isolated_generate('private-test-key','gemini-3.8-flash','test',{},runtime,worker=blocking_provider)
        except Exception as e:errors.append(e)
    thread=threading.Thread(target=run);thread.start()
    deadline=time.monotonic()+10
    while runtime.process is None and time.monotonic()<deadline:time.sleep(.01)
    process=runtime.process
    assert process is not None and process.is_alive()
    started=time.monotonic();runtime.cancel();thread.join(timeout=3)
    assert not thread.is_alive() and not process.is_alive()
    assert time.monotonic()-started<3
    assert isinstance(errors[0],JobCancelled)


def test_cancel_endpoint_preserves_completed_draft_checkpoint(tmp_path,monkeypatch):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'server.sqlite3')
    with server.db() as c:
        c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    server.state.update(key='private-test-key',keyhash='cancel-test',budget=2)
    p=server.create({'name':'Checkpoint','files':[{'name':'one.txt','text':SCRIPT}]})
    ep=p['episodes'][0]
    v=dict(angle='Saved draft',segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[dict(role=r,event_ids=[ep['events'][i]['id']],reason='Source') for r,i in [('Hook',0),('Context',1),('Escalation',1),('Cliffhanger',2)]],question='What next?')
    original=server.isolated_generate
    entered=threading.Event()
    requests=[]
    def generate(key,model,prompt,options,runtime):
        requests.append(prompt)
        if prompt.startswith('Create ONE'):
            return {'candidates':[{'content':{'parts':[{'text':json.dumps(dict(title='Saved',promo=v))}]}}]}
        entered.set()
        return original(key,model,prompt,options,runtime,worker=blocking_provider)
    monkeypatch.setattr(server,'isolated_generate',generate)
    client=TestClient(server.app);headers={'X-Studio-Token':server.TOKEN}
    jid=client.post('/api/projects/'+p['id']+'/generate',headers=headers,json={'selected_ids':[ep['id']]}).json()['job_id']
    from test_new_workflow import finish
    assert finish(client,jid)['status']=='done'
    vid=server.load(p['id'])['groups'][0]['variations'][0]['id']
    jid=client.post('/api/projects/'+p['id']+'/virality-review',headers=headers,json={'variation_id':vid}).json()['job_id']
    assert entered.wait(5)
    deadline=time.monotonic()+10
    while server.runtimes[jid].process is None and time.monotonic()<deadline:time.sleep(.01)
    process=server.runtimes[jid].process
    assert process is not None and process.is_alive()
    result=client.post('/api/jobs/'+jid+'/cancel',headers=headers,json={})
    assert result.status_code==200 and result.json()['status']=='cancelled'
    assert not process.is_alive() and jid not in server.active
    saved=server.load(p['id'])
    assert saved['groups'][0]['variations'][0]['angle']=='Saved draft'
    assert len(requests)==2  # no retry or later provider stage
    durable=client.get('/api/jobs/'+jid).json()
    assert durable['operation']=='virality-review'
    assert [s['status'] for s in durable['stages']]==['cancelled']
    assert 'private-test-key' not in json.dumps(durable)
    status=client.get('/api/status').json()
    assert status['active_jobs']==[] and status['latest_jobs'][0]['id']==jid
    assert client.post('/api/jobs/'+jid+'/cancel',headers=headers,json={}).json()['status']=='cancelled'
    with server.db() as c:
        assert c.execute('SELECT COUNT(*) FROM raw').fetchone()[0]==1
        assert c.execute('SELECT spent FROM usage').fetchone()[0] > 0  # ambiguous request stays reserved

def test_spawn_import_does_not_recover_parent_job_but_startup_does(tmp_path,monkeypatch):
    import runpy
    monkeypatch.setenv('PROMO_STUDIO_DATA',str(tmp_path))
    module=runpy.run_path(str(Path(__file__).resolve().parents[1]/'server.py'),run_name='__mp_main__')
    store=module['job_store']
    DurableJob(store,id='parent-active',project_id='p',operation='generate',body={},status='running',created_at=1,stages=[])
    child_module=runpy.run_path(str(Path(__file__).resolve().parents[1]/'server.py'),run_name='__mp_main__')
    assert child_module['job_store'].get('parent-active')['status']=='running'
    child_module['recover_jobs']()
    assert store.get('parent-active')['status']=='interrupted'


def test_cancel_queued_job_never_starts_provider(tmp_path,monkeypatch):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'queued.sqlite3')
    with server.gate:jid=server.new_job('p','generate',{})
    result=server.cancel_job(jid)
    assert result['status']=='cancelled'
    assert result['stages']==[] and jid not in server.active
    assert server.runtimes[jid].process is None
