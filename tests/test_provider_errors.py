import io,json,sys
from pathlib import Path
import urllib.error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
import provider

def http_error(code,message='High demand',status='UNAVAILABLE'):
    return urllib.error.HTTPError('https://generativelanguage.googleapis.com/test',code,'Error',{'Retry-After':'2','x-request-id':'request-123'},io.BytesIO(json.dumps({'error':{'code':code,'status':status,'message':message}}).encode()))

def test_preserves_safe_provider_details(monkeypatch):
    def fail(*args,**kwargs):raise http_error(503,'High demand. private-test-key')
    monkeypatch.setattr(provider.urllib.request,'urlopen',fail)
    with pytest.raises(provider.ProviderError) as raised:provider.Gemini('private-test-key','gemini-3.8-flash').generate('Minimal test')
    error=raised.value
    assert error.http_status==503 and error.provider_status=='UNAVAILABLE'
    assert error.retry_after==2 and error.retryable
    assert 'private-test-key' not in str(error) and 'High demand' in str(error)

def test_bad_request_is_not_retried(monkeypatch):
    def fail(*args,**kwargs):raise http_error(400,'Unsupported setting','INVALID_ARGUMENT')
    monkeypatch.setattr(provider.urllib.request,'urlopen',fail)
    with pytest.raises(provider.ProviderError) as raised:provider.Gemini('key','gemini-3.8-flash').generate('Test')
    assert not raised.value.retryable

def test_server_retry_and_failure_ledger(tmp_path,monkeypatch):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'errors.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY,body TEXT);CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);CREATE TABLE api_errors(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);')
    monkeypatch.setitem(server.state,'key','private-test-key');monkeypatch.setitem(server.state,'keyhash','test');monkeypatch.setitem(server.state,'budget',2)
    attempts=[]
    def once(self,prompt):
        attempts.append(prompt)
        if len(attempts)==1:raise provider.ProviderError(503,'UNAVAILABLE','High demand',retry_after=0)
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"ok":true}'}]}}],'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':5}}
    monkeypatch.setattr(server.Gemini,'generate',once)
    monkeypatch.setattr(server.time,'sleep',lambda _:None)
    assert server.call('test','probe','Minimal test')=={'ok':True}
    assert len(attempts)==2
    with server.db() as c:rows=c.execute('SELECT body FROM api_errors').fetchall()
    assert len(rows)==1 and json.loads(rows[0][0])['http_status']==503
    assert 'private-test-key' not in rows[0][0]

def test_long_provider_cooldown_does_not_retry(tmp_path,monkeypatch):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'cooldown.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    monkeypatch.setitem(server.state,'key','test');monkeypatch.setitem(server.state,'keyhash','test');monkeypatch.setitem(server.state,'budget',2)
    calls=[]
    def fail(self,prompt):
        calls.append(prompt)
        raise provider.ProviderError(503,'UNAVAILABLE','Retry later',retry_after=120)
    monkeypatch.setattr(server.Gemini,'generate',fail)
    monkeypatch.setattr(server.time,'sleep',lambda _:pytest.fail('Long cooldown must not block the worker'))
    with pytest.raises(provider.ProviderError):server.call('test','probe','Test')
    assert len(calls)==1

def test_retry_cannot_exceed_budget(tmp_path,monkeypatch):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'budget.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    monkeypatch.setitem(server.state,'key','test');monkeypatch.setitem(server.state,'keyhash','test');monkeypatch.setitem(server.state,'budget',.08);monkeypatch.setitem(server.state,'model','gemini-3.8-flash')
    calls=[]
    def fail(self,prompt):calls.append(prompt);raise provider.ProviderError(503,'UNAVAILABLE','High demand',retry_after=0)
    monkeypatch.setattr(server.Gemini,'generate',fail);monkeypatch.setattr(server.time,'sleep',lambda _:None)
    with pytest.raises(ValueError,match='exceed your'):server.call('test','probe','Test')
    assert len(calls)==1
