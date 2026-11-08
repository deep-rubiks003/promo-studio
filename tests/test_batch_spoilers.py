import copy
from test_core import SCRIPT
from parser import parse_episode
from workflow import check_all_spoilers

def test_all_promos_use_one_call_and_cache_unchanged_output():
    a=parse_episode('one.txt',SCRIPT);b=parse_episode('two.txt',SCRIPT.replace('letter','ring'));c=parse_episode('three.txt',SCRIPT.replace('letter','UNSELECTED'))
    def draft(ep,vid):return dict(id=vid,segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[],question='Who has it?')
    p=dict(id='p',episodes=[a,b,c],brief=dict(selected_ids=[a['id'],b['id']]),groups=[dict(source_ids=[a['id']],variations=[draft(a,'a')]),dict(source_ids=[b['id']],variations=[draft(b,'b')])])
    calls=[]
    def model(pid,stage,prompt):
        calls.append(prompt);assert 'UNSELECTED' not in prompt
        return dict(results=[dict(promo_id=i,status='clear',summary='No major reveal.',findings=[]) for i in ['a','b']])
    check_all_spoilers(p,model,lambda eps:[dict(id=e['id'],events=e['events']) for e in eps])
    assert len(calls)==1 and len(p['spoiler_batch_check']['results'])==2
    check_all_spoilers(p,model,lambda eps:eps)
    assert len(calls)==1
    p['groups'][0]['variations'][0]['question']='Changed question'
    check_all_spoilers(p,model,lambda eps:eps)
    assert len(calls)==2

def test_incomplete_batch_preserves_previous_checks():
    import pytest
    ep=parse_episode('one.txt',SCRIPT)
    p=dict(id='p',episodes=[ep],brief=dict(selected_ids=[ep['id']]),groups=[dict(source_ids=[ep['id']],variations=[dict(id='a',segments=[dict(episode_id=ep['id'],start=0,end=25)],beats=[],question='Next?')])])
    before=copy.deepcopy(p)
    with pytest.raises(ValueError):check_all_spoilers(p,lambda *args:dict(results=[]),lambda eps:eps)
    assert p==before
