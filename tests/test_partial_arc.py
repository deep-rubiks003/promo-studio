import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow import generation_prompt, refinement_prompt, DRAFT
from discovery import _review_prompt

def test_drafting_and_refinement_require_excerpt_not_complete_arc():
    prompts=[generation_prompt({},[],lambda x:x),refinement_prompt({}, {}, [],lambda x:x,'Hook','More tension'),DRAFT]
    for prompt in prompts:
        assert 'Do not require a complete emotional arc' in prompt
        assert '20–60' in prompt
        assert 'unresolved' in prompt
        assert 'mini-story' not in prompt

def test_discovery_review_does_not_require_complete_arc():
    assert 'Do not require a complete emotional arc' in _review_prompt([])
