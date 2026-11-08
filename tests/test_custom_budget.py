import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
import server
from fastapi import HTTPException

@pytest.mark.parametrize('budget',[2.01,10,100,1234.56])
def test_user_chosen_budget_has_no_two_dollar_max(monkeypatch,budget):
    for k,v in server.state.copy().items():monkeypatch.setitem(server.state,k,v)
    monkeypatch.setattr(server.Gemini,'validate',lambda self:None)
    result=server.connect(dict(key='budget-test-key',model='gemini-3.5-flash-lite',budget=budget))
    assert result['budget']==budget and server.state['budget']==budget

@pytest.mark.parametrize('budget',[0,-1,float('nan'),float('inf'),-float('inf'),True,'nonsense'])
def test_invalid_limits_fail_before_provider(monkeypatch,budget):
    monkeypatch.setattr(server.Gemini,'validate',lambda self:pytest.fail('Do not validate key for bad limit'))
    with pytest.raises(HTTPException):server.connect(dict(key='budget-test',model='gemini-3.5-flash-lite',budget=budget))


def test_budget_guard_uses_chosen_limit_and_preserves_recorded_usage(studio,monkeypatch):
    p,c,h=studio
    for k,v in server.state.copy().items():monkeypatch.setitem(server.state,k,v)
    monkeypatch.setattr(server.Gemini,'validate',lambda self:None)
    server.connect(dict(key='custom-limit-key',model='gemini-3.5-flash-lite',budget=12.5))
    with server.db() as db:db.execute('INSERT OR REPLACE INTO usage VALUES (?,?)',(server.state['keyhash'],12.499))
    monkeypatch.setattr(server.Gemini,'generate',lambda *args,**kwargs:pytest.fail('Budget must block before network'))
    with pytest.raises(ValueError,match=r'\$12.50 spending limit'):server.call(p['id'],'test-budget','small')
    server.connect(dict(key='custom-limit-key',model='gemini-3.5-flash-lite',budget=25))
    assert server.spent()==pytest.approx(12.499) and server.state['budget']==25

from test_new_workflow import studio
