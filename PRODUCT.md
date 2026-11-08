## Latest visible-drafts policy

Every episode retains its best available real draft, including weak or invalid cuts, with actual ratings (or explicitly Not rated), failed checks and repair history. Rank passed drafts first, then reviewed flagged drafts, then unrated flagged drafts. Do not silently discard an episode because its proposed cut failed. A no-source/no-candidate case is explicit and repairable, never filled with invented dialogue or timestamps. Provide Repair this episode promo within discovery. Candidate-history counts refer to attempts, not rejected episodes.

## Latest full-show correction

New discovery runs create exactly one final promo slot per uploaded episode, then rank the qualified episode promos across the show. Each cut stays inside its own episode. Long-episode windows compete for one slot; no cross-episode stitching. Every episode is represented as ready, pending or needs repair, with rejected source drafts and reasons retained where available. Legacy saved runs preserve their previous policy.

# Promo Studio

For a creative editor turning timestamped episode screenplays into posting-ready cut instructions. A successful promo is a coherent 20–60 second mini-story with a first-three-second hook, meaningful escalation and an unresolved ending. Output is source cut instructions, not rendered video.

## Two workflows

**Make promos from chosen episodes** retains the manual workflow: import locally, review episode numbers and TXT-extracted loglines without AI, select episodes, then set a brief. Create one promo across the selected sources or multiple promos assigned per episode/custom group, with exactly one draft per promo. No genre-analysis prerequisite or three-variation generation. Only assigned source episodes are sent for that promo. Source checks, rubric, reviewed recut proposals, explicit apply/keep controls and repair remain available. Applied recuts invalidate prior spoiler checks.

**Find promos across the show** is a separate full-show mode. Every newly planned run includes all uploaded episodes, regardless of manual selection. There are no episode-scope, recommended-shortlist or quantity controls. The brief contains cut structure, a 20–60 second duration range and optional creative emphasis. Rank every qualifying distinct promo in posting order; do not stop at 30 or add weak cuts to fill a target count. Existing saved runs retain their original source scope and quantity metadata and display legacy labels when applicable.

## Full-show discovery behavior

Plan locally before making model requests. Show source windows, bounded scan batches, estimated requests including fallback splits, worst-case retry cost and remaining session budget when available. A key is needed to run or resume, not to preview or open saved results. Re-preview when the brief or connected model changes.

Read source-sized sequential batches with complete dialogue turns and original timecodes. Preserve exact source retrieval, code validation before editorial review, targeted stitching, and quality/diversity ranking. Persist stages, coverage and completed work for resume; use adaptive bounded splitting when needed. No embeddings or vector database. A 50- or 100-episode input is supported by bounded planning, but provider quota and spending limits can still interrupt completion. Do not promise a complete show within a fixed $2 budget or unavailable provider capacity.

Show live job status and refresh the saved discovery report about every six seconds while the job runs. Keep partial results available. Report scanned, pending and failed windows, saved batches, candidates, review progress and interruptions explicitly. An incomplete scan must retain its coverage gaps; never label pending or failed windows as complete. Stop preserves completed work and stops the local worker; remote billing cannot be retracted. Resume uses persisted checkpoints, and restart recovery marks unfinished jobs interrupted.

Each ranked result shows its posting priority and reason, ordered episode/timecode cuts, hook, cliffhanger and unresolved ending. Exact source lines expand on demand; opening a result reveals its read-only source cut. Discovery runs are saved independently and preserve manual groups, briefs, drafts and proposals. Discovery cuts have no refinement or manual repair controls. Export the run report as JSON; an optional batch Spoiler checker reviews completed ranked results within their assigned source scope. Existing stored field names such as `shortlist` do not imply a new count limit.

## Interface and visual direction

Use the user-approved modern cinematic film-suite design restored from `outputs/Promo-Studio-GitHub/static`: obsidian ground, deep slate layers, warm amber accents, Inter-first reading type, monospaced technical references, rounded panels, ambient depth and restrained glass treatments. Active source is authoritative. No new seed, font exploration or comp is claimed.

Preserve the manual four-stage workflow and script-centered editor. Full-show discovery has a distinct premium composition within the restored world: quiet return bar, spacious editorial opening, real uploaded-source ledger, minimal search form and ruled posting-order results. Hide the manual workflow rail while in this mode. On mobile, place the search form before the initially collapsed source ledger. Use real coverage and counts, never synthetic success metrics.

Keep source-backed proposals before mutation, visible failures, keyboard focus, reduced-motion support, private local key entry and no mobile page overflow. Light PDF handoffs retain dark ink and warm amber rules.

## Review and handoff

The optional post-draft Spoiler checker uses only the promo’s assigned episodes, cites evidence and retains uncertainty. Other and future episodes are outside that check. Saved-output snapshots stay read-only and require no AI. Manual drafts export as a warm-paper PDF cut collection and retain printable HTML and JSON handoffs, including ordered cuts, dialogue, story beats, unanswered questions and available spoiler findings. Undo, four-part spoiler assessment and generation intents remain rolled back.

## Shared repair policy — November 9

Both modes use the same five local cut checks: selected source, valid cut structure, 20–60 seconds, no boundary inside a timestamped dialogue block, and hook within three seconds. Editorial review is optional and separate. Only failed local cuts appear in the multi-select repair dialog. Sequential source-scoped repairs save proposals, followed by explicit current/new comparison decisions; invalid proposals cannot replace current output. Keeping an invalid current draft warns that review and score are unavailable. Saved proposals can be reopened, and per-item failures preserve earlier work. Both modes retain PDF export and the approved slate/amber visual design.

### Choice and review override

The latest user decision makes cut checks advisory for accepting proposals and requesting optional Virality Review in both modes. Keep current and Keep new are allowed regardless of check failures when a draft exists. Review scores and failures coexist; review never marks a failed local check as passed. Old no-review/no-score confirmation is removed. Missing proposals remain unavailable.
