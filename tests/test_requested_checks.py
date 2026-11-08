import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from parser import validate_cut, WEIGHTS

def source():
    return dict(id='e',duration=35,events=[dict(id='a',start=0,end=10,speaker='A',kind='dialogue',text='I was going to'),dict(id='b',start=10,end=35,speaker='A',kind='dialogue',text='tell you the truth.')])

def cut(start=10):
    return dict(segments=[dict(episode_id='e',start=start,end=35)],beats=[dict(role=r,event_ids=['b'],reason='Source') for r in ('Hook','Context','Escalation','Cliffhanger')],question='What next?',review_status='reviewed',narrative=dict(standalone=True,escalation=True,cliffhanger=True,coherent=True),rubric=[dict(criterion=k,score=4,reason='Clear') for k in WEIGHTS])

def test_adjacent_same_speaker_blocks_are_independent():
    result=validate_cut(cut(),[source()],['e'])
    assert next(c for c in result['checks'] if c['name']=='No dialogue cut mid-line')['passed']
    assert result['eligible']

def test_cut_inside_a_single_speakers_block_still_fails():
    result=validate_cut(cut(12),[source()],['e'])
    assert not next(c for c in result['checks'] if c['name']=='No dialogue cut mid-line')['passed']

def test_removed_checks_are_not_eligibility_gates():
    v=cut();v['beats'][1]['event_ids']=['not-a-source-id']
    result=validate_cut(v,[source()],['e'])
    assert 'Source evidence' not in {c['name'] for c in result['checks']}
    assert 'Story order' not in {c['name'] for c in result['checks']}
    assert result['eligible']
