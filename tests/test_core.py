import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser import parse_episode, validate_cut

SCRIPT = '''EPISODE NUMBER: Episode 1
**ALICE:**
Start: (00:00.00) | End: (00:05.00)
"Who took my letter?"
**BOB:**
Start: (00:05.00) | End: (00:15.00)
"Your sister has it."
**ALICE:**
Start: (00:15.00) | End: (00:25.00)
"Then why is she standing outside?"
'''

def test_parse_and_boundary():
    ep = parse_episode('one.txt', SCRIPT)
    assert len(ep['events']) == 3
    v = {'segments':[{'episode_id':ep['id'],'start':1,'end':25}]}
    assert not validate_cut(v,[ep],[ep['id']])['eligible']

def test_bad_input():
    import pytest
    with pytest.raises(ValueError): parse_episode('bad.txt','no timestamps')

def test_selected_scope():
    ep = parse_episode('one.txt', SCRIPT)
    v = {'segments':[{'episode_id':ep['id'],'start':0,'end':25}]}
    assert not validate_cut(v,[ep],[])['eligible']

def test_malformed_middle_turn_is_never_silently_dropped():
    import pytest
    with pytest.raises(ValueError,match='malformed timestamps'):
        parse_episode('bad.txt',SCRIPT.replace('Start: (00:05.00)','Start: missing'))

def test_all_overlapping_dialogue_tracks_protected():
    ep=parse_episode('overlap.txt',SCRIPT+'\n**OTHER:**\nStart: (00:22.00) | End: (00:30.00)\n"Wait for me!"\n')
    result=validate_cut({'segments':[{'episode_id':ep['id'],'start':0,'end':25}]},[ep],[ep['id']])
    assert next(c for c in result['checks'] if c['name']=='No dialogue cut mid-line')['passed'] is False

def test_nonfinite_times_are_rejected():
    ep=parse_episode('one.txt',SCRIPT)
    assert not validate_cut({'segments':[{'episode_id':ep['id'],'start':0,'end':float('nan')}]},[ep],[ep['id']])['eligible']
