import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow import generation_prompt
def test_first_pass_has_editorial_and_source_contract():
    prompt=generation_prompt(dict(minimum=20,maximum=60,structure='continuous',selected_ids=['e']),[dict(id='e',events=[])],lambda x:x)
    for value in ['Hook (25%)','Standalone clarity (25%)','Escalation (15%)','Cliffhanger (25%)','Cut quality (10%)','first 3 seconds','unresolved','No editing, music','complete dialogue']:
        assert value in prompt
    assert 'promo' in prompt and 'ONLY SOURCE' in prompt
