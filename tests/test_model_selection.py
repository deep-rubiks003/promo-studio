import pytest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import provider

@pytest.mark.parametrize('model,input_rate,output_rate',[
    ('gemini-3.5-flash-lite',.30,2.50),
    ('gemini-3.1-flash-lite',.25,1.50),
])
def test_lite_models_config_and_rates(monkeypatch,model,input_rate,output_rate):
    from models import model_rates
    assert model_rates(model)==(input_rate,output_rate)
    captured=[]
    def fake(self,suffix,payload=None):captured.append((self.model,suffix,payload));return {'ok':True}
    monkeypatch.setattr(provider.Gemini,'request',fake)
    provider.Gemini('private',model).generate('Test')
    assert captured[0][0]==model
    config=captured[0][2]['generationConfig']
    assert config['responseMimeType']=='application/json'
    assert config['thinkingConfig']['thinkingLevel']=='LOW'

@pytest.mark.parametrize('model',['gemini-3.5-flash-lite','gemini-3.1-flash-lite'])
def test_connect_accepts_lite_without_generating(monkeypatch,model):
    import server
    validated=[]
    monkeypatch.setattr(server.Gemini,'validate',lambda self:validated.append(self.model))
    for key in ('key','model','budget','keyhash'):monkeypatch.setitem(server.state,key,server.state[key])
    result=server.connect(dict(key='test-key',model=model,budget=2))
    assert result['model']==model and validated==[model]

def test_unknown_model_rejected_before_api(monkeypatch):
    import server
    from fastapi import HTTPException
    monkeypatch.setattr(server.Gemini,'validate',lambda self:pytest.fail('Must reject before API'))
    with pytest.raises(HTTPException):server.connect(dict(key='test',model='gemini-unknown',budget=2))

@pytest.mark.parametrize('model,expected',[('gemini-3.5-flash-lite',.00155),('gemini-3.1-flash-lite',.001)])
def test_actual_usage_uses_selected_model_prices(tmp_path,monkeypatch,model,expected):
    import server
    monkeypatch.setattr(server,'DB',tmp_path/'prices.sqlite3')
    with server.db() as c:c.executescript('CREATE TABLE raw(id TEXT PRIMARY KEY,project_id TEXT,stage TEXT,body TEXT);CREATE TABLE usage(keyhash TEXT PRIMARY KEY,spent REAL);')
    for k,v in dict(model=model,key='test-key',keyhash='test',budget=2).items():monkeypatch.setitem(server.state,k,v)
    raw=dict(candidates=[dict(finishReason='STOP',content=dict(parts=[dict(text='{"ok":true}')]))],usageMetadata=dict(promptTokenCount=1000,candidatesTokenCount=400,thoughtsTokenCount=100))
    monkeypatch.setattr(server.Gemini,'generate',lambda *args,**kwargs:raw)
    assert server.call('test','test','Test')==dict(ok=True)
    assert server.spent()==pytest.approx(expected)
