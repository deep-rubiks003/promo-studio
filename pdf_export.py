"""Local, source-backed PDF handoff. Rendering never calls a model or edits a project."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import math
import os
import threading

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable, Flowable

from parser import validate_cut
from workflow import fingerprint

PAPER = colors.HexColor('#FFFFFF')
INK = colors.HexColor('#182532')
MUTED = colors.HexColor('#52606D')
AMBER = colors.HexColor('#F59E0B')
RULE = colors.HexColor('#DCE2E7')
_FONT_LOCK = threading.Lock()


def _fonts():
    """Embed the bundled Roboto pair; PDF generation is fully offline."""
    local=Path(__file__).parent/'fonts'
    windir=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    with _FONT_LOCK:
        if 'PromoBody' not in pdfmetrics.getRegisteredFontNames():
            regular,bold=local/'Roboto-Regular.ttf',local/'Roboto-Bold.ttf'
            if not regular.is_file() or not bold.is_file():
                raise RuntimeError('PDF export requires the bundled Roboto Regular and Bold font files in the app fonts folder.')
            pdfmetrics.registerFont(TTFont('PromoBody',str(regular)))
            pdfmetrics.registerFont(TTFont('PromoBold',str(bold)))
            pdfmetrics.registerFontFamily('PromoBody',normal='PromoBody',bold='PromoBold')
            for path in [windir/'Nirmala.ttf',Path('/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf')]:
                if path.is_file():
                    pdfmetrics.registerFont(TTFont('PromoFallback',str(path)))
                    break


def _markup(value, primary='PromoBody'):
    """Escape user text and switch fonts for glyphs absent in the main font."""
    text = str(value if value is not None else '')
    base = pdfmetrics.getFont('PromoBody').face.charToGlyph
    fallback = pdfmetrics.getFont('PromoFallback').face.charToGlyph if 'PromoFallback' in pdfmetrics.getRegisteredFontNames() else {}
    result = []
    run = ''
    last = None
    for ch in text:
        font = 'PromoFallback' if ord(ch) not in base and ord(ch) in fallback else primary
        if font != last and run:
            result.append(f'<font name="{last}">{escape(run).replace(chr(10), "<br/>")}</font>')
            run = ''
        run += ch
        last = font
    if run:
        result.append(f'<font name="{last}">{escape(run).replace(chr(10), "<br/>")}</font>')
    return ''.join(result) or 'Not provided.'


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _tc(value):
    if not _finite(value):
        return 'invalid time'
    return f'{int(value // 60):02}:{value % 60:05.2f}'


def _reviewed(draft):
    if draft.get('review_status') == 'pending':
        return False
    return draft.get('review_status') == 'reviewed' or bool(draft.get('narrative') and draft.get('rubric'))


class SourceTimeline(Flowable):
    """An honest proportional map of the ordered cut durations."""
    def __init__(self, segments):
        super().__init__()
        self.durations=[s['end']-s['start'] for s in segments if isinstance(s,dict) and _finite(s.get('start')) and _finite(s.get('end')) and s['end']>s['start']]
        self.height=56
    def wrap(self,availWidth,availHeight):
        self.width=availWidth
        return availWidth,self.height
    def draw(self):
        c=self.canv;total=sum(self.durations)
        if not total:return
        c.setFillColor(MUTED);c.setFont('PromoBody',9)
        c.drawString(0,44,'Playback order / source cuts to scale')
        x=0
        for index,duration in enumerate(self.durations):
            width=self.width*duration/total
            c.setFillColor(AMBER if index%2==0 else RULE)
            c.rect(x,12,max(width-2,1),15,stroke=0,fill=1)
            if width>30:
                c.setFillColor(INK);c.setFont('PromoBold',8);c.drawString(x+5,17,str(index+1))
            x+=width
        c.setFillColor(MUTED);c.setFont('PromoBody',9)
        c.drawString(0,0,'00:00.00');c.drawRightString(self.width,0,_tc(total))


def build_pdf(project):
    """Return a real PDF containing every draft in the supplied project/view.

    Call saved_output.view(project) first to export checkpointed generation output.
    Validation is recomputed from source, and pending reviews never earn approval.
    """
    _fonts()
    output = BytesIO()
    styles = {
        'eyebrow': ParagraphStyle('status',fontName='PromoBold',fontSize=10,leading=15,textColor=MUTED,spaceAfter=14),
        'title': ParagraphStyle('title',fontName='PromoBold',fontSize=32,leading=38,textColor=INK,spaceAfter=18,splitLongWords=True,keepWithNext=True),
        'h1': ParagraphStyle('h1',fontName='PromoBold',fontSize=26,leading=32,textColor=INK,spaceAfter=14,keepWithNext=True),
        'h2': ParagraphStyle('h2',fontName='PromoBold',fontSize=15,leading=21,textColor=INK,spaceBefore=23,spaceAfter=11,keepWithNext=True),
        'label': ParagraphStyle('label',fontName='PromoBold',fontSize=10,leading=15,textColor=INK,spaceBefore=11,spaceAfter=5,keepWithNext=True),
        'body': ParagraphStyle('body',fontName='PromoBody',fontSize=11,leading=17,textColor=INK,spaceAfter=10,splitLongWords=True,alignment=TA_LEFT),
        'small': ParagraphStyle('small',fontName='PromoBody',fontSize=9.5,leading=14,textColor=MUTED,spaceAfter=7,splitLongWords=True),
        'quote': ParagraphStyle('quote',fontName='PromoBody',fontSize=12,leading=19,textColor=INK,leftIndent=12,rightIndent=35,spaceBefore=3,spaceAfter=17,splitLongWords=True),
    }
    styles['body_keep'] = ParagraphStyle('body_keep', parent=styles['body'], keepWithNext=True)
    story = []

    def add(value, style='body'):
        # Paragraphs split across pages; source dialogue is never put in an unsplittable table.
        story.append(Paragraph(_markup(value, styles[style].fontName), styles[style]))

    def heading(value):
        add(value, 'h2')

    def field(label, value):
        add(label.upper(), 'label')
        add(value)

    brief = project.get('brief') or {}
    episodes = project.get('episodes', [])
    byid = {ep['id']: ep for ep in episodes}
    records = []
    for group in project.get('groups', []):
        for draft in group.get('variations', []):
            reviewed = _reviewed(draft)
            review_input = draft if reviewed else dict(draft, narrative={}, rubric=[])
            valid = validate_cut(review_input, episodes, group.get('source_ids', brief.get('selected_ids', [])), brief.get('minimum', 20), brief.get('maximum', 60), brief.get('structure', 'continuous'))
            label = 'Needs changes' if not valid['eligible'] else 'Eligible' if reviewed else 'Review pending'
            records.append((group, draft, valid, label))

    add(project.get('name') or 'Promo collection', 'title')
    story.append(HRFlowable(width='100%', thickness=1, color=RULE, spaceAfter=20))
    add(f'{len(records)} promo drafts / {sum(r[3] == "Eligible" for r in records)} eligible / {sum(r[3] == "Needs changes" for r in records)} need changes / {sum(r[3] == "Review pending" for r in records)} awaiting review')
    field('Cut brief', f'{str(brief.get("structure", "continuous")).capitalize()} / requested {brief.get("minimum", 20)}-{brief.get("maximum", 60)} seconds')
    if brief.get('emphasis'):
        field('Editorial emphasis', brief['emphasis'])
    if project.get('saved_output_view'):
        add('SAVED OUTPUT VIEW - checkpointed drafts; incomplete reviews remain pending.', 'small')
    summary = project.get('saved_output_summary') or {}
    if summary.get('not_generated'):
        add(f'{summary["not_generated"]} requested promo groups have no saved draft yet.', 'small')
    heading('Read before cutting')
    add('Source timestamps refer to the original episode. Promo timestamps refer to playback after the ordered cuts are assembled. Dialogue below is copied from the source script; editorial descriptions are separate.')
    add('Script timestamps are estimates. Verify footage, audio sync and boundaries before editing. Editorial scores are rubric assessments, not viral probabilities. Unchecked spoilers are not cleared. This PDF is rendered locally from saved data; no new AI review is performed.', 'small')
    episode_results=(project.get('discovery_report') or {}).get('episode_results',[])
    if episode_results:
        missing=[row for row in episode_results if not row.get('draft')]
        heading('Episode accounting')
        add(f'{len(episode_results)} episodes / {len(records)} available source drafts / {len(missing)} with no usable returned draft.')
        for row in missing:
            add(f'Episode {row.get("number", row.get("episode_number", "?"))} / {row.get("status", "needs_repair")} / '+ '; '.join(row.get('reasons') or ['No usable source draft returned; try episode repair.']), 'small')
    heading('Draft index')
    if not records:
        add('No promo drafts are available to export.')
    for index, (group, draft, valid, label) in enumerate(records, 1):
        add(f'{index:02} / {draft.get("angle") or group.get("title") or "Untitled promo"} — {label} / {valid["duration"]:.2f}s', 'small')

    for index, (group, draft, valid, label) in enumerate(records, 1):
        story.append(PageBreak())
        add(f'{index:02} / '+(draft.get('angle') or group.get('title') or 'Untitled promo'), 'h1')
        story.append(HRFlowable(width='100%', thickness=1, color=RULE, spaceAfter=14))
        add(f'{label.upper()} / {valid["duration"]:.2f} seconds / {brief.get("structure", "continuous")}', 'eyebrow')
        if label == 'Review pending':
            add('Draft saved; editorial review has not completed. No score or approval is available.', 'small')
        elif valid.get('rubric_score') is not None:
            add(f'Editorial rubric: {valid["rubric_score"]}/100. Approval: {label}. Spoiler review is reported separately.', 'small')
        story.append(SourceTimeline(draft.get('segments') or []))
        if group.get('posting_priority') is not None:
            field('Posting priority', '#'+str(group['posting_priority'])+' / '+label)
            field('Ranking reason', group.get('posting_reason') or 'Review the cut checks before posting.')
        field('Creative intent', draft.get('distinction'))
        field('Posting rationale', draft.get('reason') or group.get('opportunity'))
        field('Unanswered question', draft.get('question'))
        heading('Ordered source cuts and original dialogue')
        offset = 0
        for cut, segment in enumerate(draft.get('segments') or [], 1):
            if not isinstance(segment, dict):
                field(f'Cut {cut:02} / invalid segment', repr(segment))
                continue
            ep = byid.get(segment.get('episode_id'))
            start, end = segment.get('start'), segment.get('end')
            usable = ep and _finite(start) and _finite(end) and 0 <= start < end <= ep['duration'] + .001
            add(f'CUT {cut:02} / Episode {ep.get("number", "?") if ep else "unknown"}', 'label')
            add(f'SOURCE  {_tc(start)} - {_tc(end)}', 'small')
            if not usable:
                add('Invalid source range. Verify or replace this cut; no playback timing or dialogue has been inferred.')
                offset = None
                continue
            add(f'PROMO   {_tc(offset)} - {_tc(offset + end - start)}' if offset is not None else 'PROMO   Unavailable after an invalid preceding source range.', 'small')
            add(ep.get('filename') or ep.get('title') or ep['id'], 'small')
            turns = [e for e in ep['events'] if e['kind'] == 'dialogue' and e['start'] < end and e['end'] > start]
            if not turns:
                add('No dialogue overlaps this range. Inspect the original source action.', 'small')
            for turn in turns:
                add(f'{turn["speaker"]} / source {_tc(turn["start"])} - {_tc(turn["end"])}', 'label')
                if turn['start'] < start - .001 or turn['end'] > end + .001:
                    add('Boundary overlap: full original turn follows, including words outside the selected cut. Repair this boundary before editing.', 'small')
                add(turn['text'], 'quote')
            if offset is not None:
                offset += end - start
        heading('Story beats / source evidence')
        for beat in valid['evidence']:
            add(beat['role'].upper(), 'label')
            add(beat['reason'], 'body_keep')
            if not beat['events']:
                add('No valid source evidence for this beat.', 'small')
            for event in beat['events']:
                ep = next((ep for ep in episodes if any(x['id'] == event['id'] for x in ep['events'])), None)
                add(f'Episode {ep["number"] if ep else "?"} / source {_tc(event["start"])} - {_tc(event["end"])} / {event["speaker"]} / event {event["id"]}', 'small')
        heading('Checks / editorial review')
        for check in valid['checks']:
            add(f'{"PASS" if check["passed"] else "FAIL"} / {check["name"]}', 'label')
            add(check['detail'], 'small')
        if label != 'Review pending':
            for rubric in draft.get('rubric') or []:
                field(f'{rubric.get("criterion", "Criterion")} / {rubric.get("score", "unscored")}/5', rubric.get('reason'))
        else:
            add('Rubric scores withheld: editorial review is pending.', 'small')
        field('Editor concern', draft.get('weakness'))
        if draft.get('review_notes'):
            field('Review notes', draft['review_notes'])
        for warning in valid.get('warnings', []):
            add(warning, 'small')
        heading('Spoiler review')
        spoiler = draft.get('spoiler_check')
        if not spoiler:
            add('NOT CHECKED / This draft has no saved spoiler assessment. Unchecked does not mean cleared.')
        else:
            stale = spoiler.get('draft_fingerprint') and spoiler['draft_fingerprint'] != fingerprint(draft)
            field('Assessment status', ('OUTDATED - ' if stale else '') + str(spoiler.get('status', 'inconclusive')).upper())
            if stale:
                add('The saved assessment refers to an earlier draft. Recheck the current cut before relying on it.', 'small')
            add(spoiler.get('summary'))
            for check in spoiler.get('checks', []):
                field(f'{check.get("category", "Check")} / {check.get("status", "unknown")}', check.get('reason'))
            for finding in spoiler.get('findings', []):
                field(f'{finding.get("severity", "unrated")} / {finding.get("category") or finding.get("kind") or "Finding"}', finding.get('explanation'))
                field('Suggestion', finding.get('suggestion'))
                for event in finding.get('events', []):
                    add(f'Episode {event.get("episode_number", "?")} / source {_tc(event.get("start"))} - {_tc(event.get("end"))} / {event.get("speaker", "Source")}', 'label')
                    add(event.get('text'), 'quote')
            add(spoiler.get('limitation') or 'AI assessment against assigned selected episodes only. Unselected episodes and footage are not checked.', 'small')
        add('EDITOR VERIFICATION / Check source footage, speaker timing, transition quality and spoiler risk before publishing.', 'small')

    def page(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, width, height, stroke=0, fill=1)
        canvas.setStrokeColor(RULE)
        canvas.line(60, height - 40, width - 60, height - 40)
        canvas.line(60, 40, width - 60, 40)
        canvas.setFont('PromoBody', 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(60, height - 29, 'PROMO STUDIO / SOURCE CUT COLLECTION')
        canvas.drawString(60, 25, 'EDITOR HANDOFF / SCRIPT TIMING REQUIRES FOOTAGE VERIFICATION')
        canvas.drawRightString(width - 60, 25, f'{doc.page:02}')
        canvas.restoreState()

    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=60, rightMargin=60, topMargin=66, bottomMargin=64, title=str(project.get('name') or 'Promo Studio editor handoff'), author='Promo Studio', pageCompression=1)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return output.getvalue()
