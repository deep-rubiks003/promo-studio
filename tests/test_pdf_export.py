"""PDF content, review-state honesty and long-source pagination regressions."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser import WEIGHTS
from pdf_export import build_pdf
from saved_output import view

fitz = pytest.importorskip('pymupdf')


def sample_project(long=False):
    events = [dict(id=f'e:{i}', start=a, end=b, speaker=s, kind='dialogue', text=t) for i, (a, b, s, t) in enumerate([
        (0, 5, 'ALICE', '“Who took my letter?”'),
        (5, 15, 'BOB', 'Your sister has it. Café <door> & a clue.'),
        (15, 25, 'ALICE', 'Then why is she standing outside?'),
    ])]
    if long:
        events[1]['text'] = '\n'.join(f'Source line {i:03}: the letter waits beside the door; nobody knows who left it.' for i in range(1, 181))
    episode = dict(id='e', number=7, title='Episode 7', filename='episode-7.txt', duration=25, events=events)
    draft = dict(id='v1', angle='The letter at the door', distinction='A missing letter becomes a mystery about the person outside.', reason='A direct question opens the scene, and a new uncertainty closes it.', segments=[dict(episode_id='e', start=0, end=25)], beats=[dict(role=role, event_ids=[events[i]['id']], reason=reason) for role, i, reason in [
        ('Hook', 0, 'A concrete missing object creates immediate curiosity.'),
        ('Context', 1, 'The sister has the letter.'),
        ('Escalation', 1, 'The answer moves the conflict closer to home.'),
        ('Cliffhanger', 2, 'The person outside creates an unanswered question.'),
    ]], question='Why is the sister waiting outside?', weakness='The last pause needs a clean picture cut.', review_status='reviewed', narrative=dict(standalone=True, escalation=True, cliffhanger=True, coherent=True), rubric=[dict(criterion=k, score=4, reason='Source-supported editorial judgment.') for k in WEIGHTS])
    pending = copy.deepcopy(draft)
    pending.update(id='v2', angle='Saved but awaiting review', review_status='pending')
    needs = copy.deepcopy(draft)
    needs.update(id='v3', angle='A boundary to repair', segments=[dict(episode_id='e', start=1, end=25)])
    return dict(id='p', name='The Letter / Promo Collection', episodes=[episode], brief=dict(selected_ids=['e'], minimum=20, maximum=60, structure='continuous'), groups=[dict(id='g', title='The letter', source_ids=['e'], variations=[draft, pending, needs])])


def extracted(data):
    with fitz.open(stream=data, filetype='pdf') as document:
        # System fonts can wrap the same words differently on Windows/Linux.
        # Content assertions ignore layout whitespace; geometry tests below
        # independently retain strict bounds and pagination checks.
        return '\n'.join(' '.join(page.get_text().split()) for page in document)


def test_pdf_contains_every_draft_original_dialogue_checks_and_honest_states():
    project = sample_project()
    before = copy.deepcopy(project)
    data = build_pdf(project)
    assert data.startswith(b'%PDF-')
    assert project == before
    text = extracted(data)
    for expected in ['The Letter / Promo Collection', 'ELIGIBLE', 'REVIEW PENDING', 'NEEDS CHANGES', 'SOURCE 00:00.00 - 00:25.00', 'PROMO 00:00.00 - 00:25.00', 'ALICE', '“Who took my letter?”', 'Café <door> & a clue.', 'Episode 7', 'Unanswered question'.upper(), 'Source-supported editorial judgment.', 'No dialogue cut mid-line', 'NOT CHECKED', 'Boundary overlap', 'footage']:
        assert expected in text
    pending = text.split('Saved but awaiting review')[-1].split('A boundary to repair')[0]
    assert 'No score or approval is available.' in pending
    assert 'Editorial rubric:' not in pending
    assert 'Rubric scores withheld' in pending
    assert 'Complete editorial review' not in pending
    with fitz.open(stream=data, filetype='pdf') as document:
        assert len(document) >= 4
        assert any(font[1] == 'ttf' for page in document for font in page.get_fonts())
        for label in ['The letter at the door', 'Saved but awaiting review', 'A boundary to repair']:
            assert any(label in page.get_text() and 'PROMO 0' in page.get_text() for page in document)


def test_long_source_dialogue_flows_across_pages_without_omission():
    project = sample_project(long=True)
    project['groups'][0]['variations'] = project['groups'][0]['variations'][:1]
    data = build_pdf(project)
    text = extracted(data)
    for i in range(1, 181):
        assert f'Source line {i:03}:' in text
    assert 'Then why is she standing outside?' in text
    assert 'Spoiler review' in text
    with fitz.open(stream=data, filetype='pdf') as document:
        assert len(document) > 5
        for page in document:
            # Header/footer are fixed; content must stay inside the page frame.
            for word in page.get_text('words'):
                assert word[0] >= 45 and word[2] <= page.rect.width - 44
                assert word[1] >= 12 and word[3] < page.rect.height - 12


def test_saved_output_projection_keeps_pending_review_unapproved():
    project = sample_project()
    project['pending_generation'] = dict(brief=project['brief'], groups=[dict(title='Checkpoint', source_ids=['e'], variations=[project['groups'][0]['variations'][1]])])
    text = extracted(build_pdf(view(project)))
    assert 'SAVED OUTPUT VIEW' in text
    assert 'REVIEW PENDING' in text
    assert 'Editorial rubric:' not in text
    assert 'No score or approval is available.' in text


def test_spoiler_evidence_limitations_and_stale_assessment_are_visible():
    project = sample_project()
    draft = project['groups'][0]['variations'][0]
    draft['spoiler_check'] = dict(status='warning', draft_fingerprint='earlier-version', summary='Possible identity reveal.', limitation='Only the assigned episode was checked.', findings=[dict(severity='high', kind='Identity reveal', explanation='The clip discloses the holder of the letter.', suggestion='Review the revealing line.', events=[dict(project['episodes'][0]['events'][1], episode_number=7)])])
    text = extracted(build_pdf(project))
    for expected in ['OUTDATED - WARNING', 'earlier draft', 'IDENTITY REVEAL', 'Review the revealing line.', 'Only the assigned episode was checked.', 'Café <door> & a clue.']:
        assert expected in text


def test_empty_collection_and_invalid_ranges_produce_readable_report():
    project = sample_project()
    project['groups'] = []
    assert 'No promo drafts are available' in extracted(build_pdf(project))
    project = sample_project()
    project['groups'][0]['variations'][0]['segments'] = [dict(episode_id='missing', start=0, end=10), dict(episode_id='e', start=0, end=25)]
    text = extracted(build_pdf(project))
    assert 'Invalid source range.' in text
    assert 'Unavailable after an invalid preceding source range.' in text
