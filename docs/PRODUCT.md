# Product and problem-solving decisions

## The problem

An editor must inspect a show and find promotional moments manually. The useful output is concrete: which episode, which start/end ranges, what grabs attention, what remains unresolved and why the moment is worth posting.

The tool narrows this work to inspectable source cuts. It does not add music, overlays, rewritten lines or generated video. A continuous promo is a single range; a stitched promo combines a small number of causally connected ranges in playback order.

## Who uses it and what they receive

An editor uploads scripts, controls which source may be sent to the model, and receives candidate cuts with the source dialogue in view. The handoff contains ordered source and playback timings, four beat descriptions, the unanswered question, rationale, tradeoffs, local checks and any requested reviews.

The user can inspect failures rather than losing the whole batch when one draft is poor. The system retains saved cuts after outages or quota exhaustion, and a proposed refinement does not replace the current cut until explicitly applied.

## The workflows

### 1. Manual creation

- One promo from a pool of selected episodes.
- One promo per selected episode.
- Custom groups of selected episodes, with one draft per group.
- Continuous or stitched ranges with a requested duration within 20–60 seconds.

Manual runs are limited to ten nonempty promo groups. Selected episodes must be assigned exactly once to their groups. A group is a permissible source pool, not a requirement to use every episode.

### 2. Find promos across the show

The discovery planner scans every assigned source window within conservative context limits. It records scan coverage, source-valid candidates and rejections. It can make one targeted stitching attempt from linked opportunities. It initially returns unranked candidates; the editor requests review when ready to score and shortlist them.

### 3. Refine rather than overwrite

The editor can target a part or the entire promo and request a source-supported change. The model returns a complete proposed cut with a change explanation and tradeoff. The current version stays in place until Apply. Unsupported directions must not cause invented dialogue.

### 4. Optional assessments

**Virality Review** examines the actual clip as a newcomer and returns weighted editorial scores. It is deliberately not automatic after generation or refinement. The name describes a creative review; its score is not a calibrated viral probability.

**Spoiler checker** checks all final promos together against their respective assigned episodes. It is not a full-show spoiler guarantee when only some episodes are assigned, and it does not infer unseen future episodes. Applying a recut invalidates stale findings.

## Why these decisions

| Decision | Reason and tradeoff |
|---|---|
| Import metadata without AI | Supplied fields should not cost requests or be creatively rewritten. Missing data remains missing. |
| One draft, not three automatic variations | Keeps the first workflow focused and reduces generated output and review work. It does not imply that an earlier multi-variation response always used three calls. |
| Excerpt, not a complete emotional arc | A full arc can push the model toward a whole scene and resolve the tension the promo should preserve. |
| Separate local validity and editorial review | A correct timestamp range can still be a poor promo. Review cost should be explicit rather than required just to inspect a draft. |
| Keep original source beside the proposal | The editor can judge the cut against what the show actually contains. |
| Require explicit Apply | AI suggestions should be reviewable before changing saved work. |
| Retain failures and raw responses | Inspectable failures make diagnosis possible and prevent selective reporting of only attractive successes. |
| Local-first storage | Fits a laptop prototype and avoids a multi-user deployment pretending to be ready. |

## Assumptions and limits

- Timestamps are estimates supplied by the script, not verified audiovisual measurements.
- Standalone clarity requires only the context needed for this moment, not the entire backstory.
- A complete individual dialogue block must not be cut midway; neighbouring same-speaker blocks need not be joined.
- Removing Source evidence and Story order as local gates reduces false blocking, but also reduces automatic guarantees. Coherence remains part of editorial review and human judgement.
- Automatic quantity uses heuristic thresholds. The ideal number of promos is not known in advance or scientifically learned.
- No audience-performance model, validated time savings or exhaustive show-wide search is claimed.

## How it was built with AI assistance

Codex/ChatGPT supported requirement refinement, code drafting, tests and diagnosis. Antigravity supported frontend iteration. Superpowers supported planning/debugging/test discipline, and Impeccable supported interface direction. Gemini is the runtime model integration.

The practical process was small end-to-end changes, saved-response inspection, failing regression tests before fixes, backups and browser checks. AI-generated code and explanations were checked against source and observed behaviour. Tools being installed was not treated as evidence that the product worked.

## How success should be measured next

Use a fixed set of editor-labelled episodes and compare:

1. Percentage of proposed cuts passing deterministic checks on the first response.
2. Editor acceptance and revision rate, with written rejection reasons.
3. Time to a usable shortlist versus the manual baseline.
4. Agreement between model rubric scores and independent editors.
5. Duplicate rate and coverage across distinct conflicts/episodes.
6. Once published, actual audience retention and conversion, assessed separately from editorial scores.

These are proposed evaluation measures, not achieved results.
