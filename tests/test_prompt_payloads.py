"""Lossless outbound payload contracts; no network or model dependency."""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prompt_payload import pack_events, unpack_events, payload_json, draft_context


def events():
    return [dict(id=f'canonical-event-{i}', start=i*5.125, end=(i+1)*5.125,
                 speaker='ALICE (worried)', kind='dialogue' if i % 2 else 'action',
                 text='"Never shorten this!"\nShe looks away. हिंदी 中文 😀') for i in range(20)]


def test_lossless_nested_events_and_immutable_input():
    source = [dict(episode_id='ep-immutable', events=events()),
              dict(ordered_clip=[dict(segment=dict(start=0, end=25), events=events())])]
    before = copy.deepcopy(source)
    packed = pack_events(source)
    assert packed != source and source == before
    assert unpack_events(json.loads(payload_json(source))) == before
    assert len(payload_json(source)) < len(json.dumps(source, ensure_ascii=False, separators=(',', ':'))) * .8
    assert 'canonical-event-19' in payload_json(source)


def test_preserves_playback_order_and_all_extra_fields():
    source = events()
    for i, event in enumerate(source):
        event.update(episode_id='later' if i < 10 else 'earlier', episode_number=2,
                     episode_title='Full title', segment_index=i//10,
                     playback_start=i*5.125, playback_end=(i+1)*5.125)
    assert unpack_events(pack_events(source)) == source
    irregular = copy.deepcopy(source)
    irregular[0]['optional_field'] = None
    assert unpack_events(pack_events(irregular)) == irregular
    assert unpack_events(pack_events([])) == []


def test_refinement_keeps_editorial_content_not_saved_approval_metadata():
    draft = dict(angle='Opening', distinction='Mystery', segments=[dict(episode_id='e', start=0, end=25)],
                 beats=[dict(role='Hook', event_ids=['canonical-event-0'], reason='Full reason')],
                 question='Who?', reason='Why post', weakness='Less setup',
                 validation=dict(eligible=False, checks=[dict(name='Duration', passed=False, detail='Too long')]),
                 narrative=dict(standalone=False, coherent=True), review_notes='Missing relationship.',
                 rubric=[dict(criterion='Standalone clarity', score=2, reason='Relationship unstated')],
                 spoiler_check=dict(findings=[dict(events=events())]), rejected_drafts=[dict(events=events())])
    before = copy.deepcopy(draft)
    lean = draft_context(draft)
    for key in ('angle', 'distinction', 'segments', 'beats', 'question', 'reason', 'weakness'):
        assert lean[key] == draft[key]
    assert lean['review_feedback']['review_notes'] == draft['review_notes']
    assert lean['review_feedback']['rubric'] == draft['rubric']
    assert lean['review_feedback']['narrative'] == draft['narrative']
    assert lean['review_feedback']['failed_checks'] == ['Duration: Too long']
    assert 'validation' not in lean and 'spoiler_check' not in lean and 'rejected_drafts' not in lean
    assert draft == before


def test_generation_has_one_ruleset_and_lossless_selected_source():
    from workflow import generation_prompt
    source = [dict(id='selected', events=events())]
    prompt = generation_prompt(dict(minimum=20, maximum=60, structure='continuous'), source, lambda x:x)
    assert prompt.startswith('Create ONE')
    assert unpack_events(json.loads(prompt.split(' ONLY SOURCE:', 1)[1])) == source
    assert 'event_columns' in prompt
    assert prompt.count('Hook (25%)') == 1
    assert prompt.count('Produce exactly ONE draft, never alternatives or variations.') <= 1
    prefix = prompt.split(' ONLY SOURCE:', 1)[0]
    assert len(prefix) < 3400


def test_repair_context_omits_duplicated_source_history():
    from workflow import generation_prompt
    rejected = dict(angle='Rejected', segments=[], beats=[], question='Who?',
                    validation=dict(checks=[dict(name='Bounds', detail='Too long', passed=False)]),
                    rejected_drafts=[dict(marker='DO_NOT_RESEND_HISTORY', events=events())])
    prompt = generation_prompt({}, [], lambda x:x, rejected)
    assert 'Bounds: Too long' in prompt and 'DO_NOT_RESEND_HISTORY' not in prompt


def test_refinement_prompt_keeps_critique_and_only_assigned_source():
    from workflow import refinement_prompt
    source = [dict(episode_id='selected', events=events())]
    old = dict(angle='Mystery', segments=[], beats=[], question='Who?',
               rubric=[dict(criterion='Hook', score=2, reason='Opening too slow')],
               review_notes='Establish relationship.', validation=dict(checks=[]),
               spoiler_check=dict(marker='DO_NOT_RESEND_OLD_SPOILER'))
    prompt = refinement_prompt(old, dict(minimum=20,maximum=60,structure='continuous',
                              selected_ids=['selected'], episode_groups=[['unselected']]),
                              source, lambda x:x, 'Hook', 'Make the opening tense')
    current = json.loads(prompt.split(' Current:',1)[1].split(' Brief:',1)[0])
    assert current['review_feedback']['rubric'] == old['rubric']
    assert current['review_feedback']['review_notes'] == old['review_notes']
    assert 'DO_NOT_RESEND_OLD_SPOILER' not in prompt and 'unselected' not in prompt
    assert unpack_events(json.loads(prompt.split(' Source:',1)[1])) == source


def test_discovery_scan_preserves_every_window_event_for_100_episodes():
    from discovery import plan_discovery, _prompt
    episodes = [dict(id=f'ep{i}', duration=103, events=events()) for i in range(100)]
    project = dict(episodes=episodes)
    plan = plan_discovery(project,dict(selected_ids=[e['id'] for e in episodes]),'gemini-3.5-flash-lite')
    byid = {w['id']:w for w in plan['windows']}
    before = copy.deepcopy(plan)
    for batch in plan['batches']:
        windows = [byid[i] for i in batch['window_ids']]
        prompt = _prompt('discovery-scan', plan, windows)
        assert unpack_events(json.loads(prompt.split(' EXACT SOURCE WINDOWS:',1)[1])) == windows
        assert len(prompt.encode('utf-8')) <= plan['max_prompt_bytes']
    assert plan == before


def test_batch_spoilers_preserve_playback_and_deduplicate_assigned_context():
    from workflow import check_all_spoilers, spoiler_source
    from parser import parse_episode
    from test_core import SCRIPT
    a = parse_episode('one.txt', SCRIPT)
    b = parse_episode('two.txt', SCRIPT.replace('letter', 'necklace'))
    excluded = parse_episode('three.txt', SCRIPT.replace('letter', 'DO_NOT_SEND_UNSELECTED'))
    v = dict(id='draft', segments=[dict(episode_id=b['id'],start=0,end=25),
                                  dict(episode_id=a['id'],start=0,end=25)], beats=[],question='Who?')
    p = dict(id='p', episodes=[a,b,excluded], brief=dict(selected_ids=[a['id'],b['id']]),
             groups=[dict(source_ids=[a['id'],b['id']],variations=[v])])
    captured = []
    def call(pid,stage,prompt):
        captured.append(prompt)
        return dict(results=[dict(promo_id='draft',status='clear',summary='Clear',findings=[])])
    check_all_spoilers(p,call,lambda x:x)
    assert len(captured) == 1 and 'DO_NOT_SEND_UNSELECTED' not in captured[0]
    promos = unpack_events(json.loads(captured[0].split(' FINAL PROMOS:',1)[1].split(' SOURCE CONTEXT (deduplicated):',1)[0]))
    assert promos[0]['ordered_events'] == spoiler_source(p,v,[a['id'],b['id']])[2]
    context = unpack_events(json.loads(captured[0].split(' SOURCE CONTEXT (deduplicated):',1)[1]))
    assert context == [a,b]


def test_independent_review_sees_clip_not_source_or_generator_explanations(monkeypatch):
    import server
    from parser import parse_episode
    from test_core import SCRIPT
    from test_rejected_repair import draft, review_result
    ep = parse_episode('one.txt', SCRIPT)
    ep['events'].append(dict(id='outside',start=90,end=100,speaker='ALICE',kind='dialogue',text='DO_NOT_SEND_OUTSIDE_CUT'))
    ep['duration'] = 100
    p = dict(id='p',episodes=[ep])
    v = draft(ep)
    v['reason'] = 'DO_NOT_SEND_GENERATOR_PITCH'
    captured = []
    def call(pid,stage,prompt):
        captured.append(prompt)
        assert stage == 'independent-clip-review'
        return review_result()
    monkeypatch.setattr(server,'call',call)
    server.review(p,v,dict(selected_ids=[ep['id']],minimum=20,maximum=60,structure='continuous'))
    assert len(captured) == 1
    assert 'DO_NOT_SEND_OUTSIDE_CUT' not in captured[0] and 'DO_NOT_SEND_GENERATOR_PITCH' not in captured[0]
    clip = unpack_events(json.loads(captured[0].split(' Clip: ',1)[1]))
    assert clip == [dict(segment=v['segments'][0],events=[e for e in ep['events'] if e['start']>=0 and e['end']<=25])]
    assert v['validation']['eligible']
