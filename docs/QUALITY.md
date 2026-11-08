# Quality, testing and known limitations

## Three different questions

1. **Is this range technically usable?** Local source, duration, structure, dialogue-boundary and hook-timing checks answer this.
2. **Does the excerpt work as a promo?** Optional clip-only editorial review and a human editor answer this.
3. **Will people actually watch or convert?** Only audience data can answer this. The application does not measure viral probability.

Keeping these separate avoids presenting a structurally correct clip as an automatically good advertisement.

## The local rules

The current gates are selected-episode scope/source bounds, continuous or stitched structure, total duration of 20–60 seconds, no boundary inside an individual timestamped dialogue block, and hook evidence within the first three playback seconds.

Recognized source citations are displayed, but Source evidence and Story order are no longer hard local gates. Editorial completion and standalone/coherence assessment are also separate from local eligibility. This is an intentional product tradeoff, not proof that all generated citations or joins are correct.

## Optional rubric

Virality Review scores Hook (25%), Standalone clarity (25%), Escalation (15%), Cliffhanger (25%) and Cut quality (10%), each from 0 to 5. The weighted score is the sum of `score * weight / 5`.

The reviewer must assess the actual clip. An explanation that says a cliffhanger is unresolved cannot override dialogue that already answers it. The prompt asks for minimal necessary context and an unresolved turn, not a complete emotional arc. A high score is a model judgement, not an editor-validated benchmark.

Discovery ranking additionally requires a 70/100 base score and 3/5 on clarity and cliffhanger. Repeated source episodes incur a 3-point adjustment per prior selection sharing an episode; source overlap can subtract up to 15 points. These are transparent heuristics rather than learned performance estimates.

## Observed failures and responses

| Failure observed in development | Response | Remaining limitation |
|---|---|---|
| Model selected long dramatic scenes: 107.4s, 82s and 113.3s. | Clarified excerpt-first selection and retained strict local duration validation. | Prompt changes alone do not enforce legal selection; invalid drafts still occur. |
| Same-speaker adjacent blocks were forced together based on punctuation. | Narrowed the gate to boundaries inside individual timestamped dialogue blocks. | Script blocks may not perfectly match audiovisual speech turns. |
| Refinement returned an unexpected object layout or only a description. | Normalize complete observed layouts; reject incomplete cuts; keep current output. | Model JSON can still be invalid or incomplete. |
| Provider busy responses interrupted progress. | Bounded retries, safe diagnostics, budget guard and saved checkpoints. | External availability and billing cannot be guaranteed. |
| Early optimization estimate implied more saving than shipped. | Report measured character reductions of about 29%, not the proposed 43%. | Token and real billing savings were not measured. |
| Unreviewed drafts appeared to fail editorial checks. | Removed those checks from local eligibility and added an explicit review action. | A local pass still needs editorial judgement before publication. |

The latest five-episode live project inspected during documentation had one locally valid 32.4-second draft, receiving 92/100 in an optional model review. The four other durations were 63, 77.9, 61 and 100.8 seconds, and remained flagged. This is a small observed result, not a benchmark. Confidential source dialogue and raw project outputs are intentionally excluded here.

There is no completed all-50-episode live quality benchmark claimed by this repository. The planner and tests supporting large inputs should not be confused with proof that the entire input pack produced a useful ranked shortlist.

## Offline verification

The source installation's current suite contains 110 tests. The packaged repository reruns those tests after removing a laptop-specific test import. They cover parsing, source scope, preservation of payload content, grouping, model response normalization, explicit review, spoiler batching, discovery coverage on 50/100 synthetic episodes, request bounds, storage, cancellation, recovery and exports.

Provider responses in tests are mocked or synthetic. No live Gemini key is needed. Passing tests demonstrate application contracts, not that a real model follows its prompts or that an editor agrees with its scores.

## Next quality improvements

- Constrain candidate construction to duration-legal, dialogue-safe boundaries while preserving creative choice.
- Build editor-labelled examples and evaluate first-pass local validity, acceptance and revision reasons.
- Review failures across different genres, overlapping dialogue, sparse timestamps, cross-episode references and stitched transitions.
- Compare score agreement across editors and model configurations rather than assuming one reviewer is objective.
- Run a complete show and retain all raw outputs and failures before claiming show-wide quality.
- Measure actual editing time saved and audience outcomes separately.

These are proposed next steps, not features already validated.
