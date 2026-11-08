# API calls, payloads and recovery

## Current call accounting

For a normal manual run with `G` groups, drafting uses `G` model requests. If the editor reviews `R` individual cuts, requests `F` recuts, and runs one batch spoiler assessment, the logical total is:

```text
G draft calls + R optional review calls + F recut calls + 1 optional spoiler call
```

Omit actions that were not requested. Optional genre analysis, explicit repairs and bounded provider retries add requests. Refinements do not automatically trigger review. Discovery has different accounting: one scan per bounded batch, at most one targeted stitching request, then explicitly requested clip-review batches.

Moving review behind a button removes the obligatory extra editorial request after each locally valid manual draft. It is not a guarantee that the user will spend less if they request every available assessment.

## What stays local

Metadata parsing, episode selection, exact timestamps, local validity checks, saved views and document exports do not require model requests. Upload is not coupled to genre analysis. Only assigned source is used in manual model actions.

## Lossless event tables

Verbose event JSON repeats `id`, `start`, `end`, `speaker`, `kind` and `text` for every record. The outbound serializer can share one header and use rows:

```json
{
  "event_columns": ["id", "start", "end", "speaker", "kind", "text"],
  "event_rows": [
    ["example-event-1", 0, 5, "MAYA", "dialogue", "This parcel has my name on it."]
  ]
}
```

The example illustrates the representation; a tiny table is only used when it is actually smaller than ordinary objects. Heterogeneous records remain objects. Text, source IDs, numeric times, kinds, speakers and additional playback fields remain intact. Canonical storage and exports are not converted into lossy summaries.

The prompt explains the header/row relationship, and offline round-trip tests reconstruct the original records exactly.

## Measured reductions

| Payload | Before | Implemented | Character reduction |
|---|---:|---:|---:|
| Three-episode source material | 26,273 | 18,544 | 29.4% |
| Fifty-episode source material | 370,849 | 264,585 | 28.7% |
| Three-episode generation prompt, at the measurement point | 30,761 | 21,571 | 29.9% |
| Current-draft refinement object in the measured example | 5,607 | 2,098 | 62.6% |

These are development-installation character counts, not measured token counts. Subsequent prompt wording changed, so the recorded full-prompt comparison is a snapshot rather than a permanent size promise. Private source packs are not supplied to reproduce these exact counts; public tests verify the lossless mechanism on synthetic fixtures.

An earlier proposed representation suggested roughly 43% fewer characters. The shipped representation did not achieve that number. It retains original IDs and explicit field headers; about 29% is the observed source-payload reduction. No 43% token, billing or latency saving is claimed.

## Context discipline

- The generator receives the assigned source and brief.
- The refinement request includes the existing cut, creative intent and useful critique; it excludes large saved UI histories and duplicated validation evidence.
- Independent editorial review sees the clip itself, without the generator's rationale or full-show context supplying missing clarity.
- Spoiler checking needs assigned episode context and ordered promo playback. Context shared by multiple promos is deduplicated.
- Original source remains available when proposing alternative cuts; compressing away the dialogue would harm the task.

## Batching and caches

Discovery bounds source-window batches and review batches rather than sending a whole large show in one uncontrolled request. Its deterministic cache keys include relevant source/configuration/model/version information. Changing the policy version can invalidate prior cache entries; changing source or model must not silently reuse unrelated assessments.

The spoiler batch fingerprint includes draft segments, beats, question and source scope. Applying a recut or changing output invalidates it. An unchanged check can reuse the saved result with no new model call.

No paid provider context-cache storage has been introduced. Local completed-stage reuse is different from provider-side cached-token pricing.

## Budget and failures

The app accepts a positive budget up to $2 in the current connection UI. Usage is cumulative for a key fingerprint in this local installation. Reservations use conservative request-size/rate assumptions. Successful provider usage metadata adjusts the reservation; uncertain failed requests can remain held. The provider's bill is authoritative.

`models.py` contains configured budget rates, not a live pricing feed. Verify identifiers, account availability, supported request settings and rates with the provider before relying on them for a new deployment.

Temporary 500/502/503/504 errors get at most one automatic retry within the accounting guard. Invalid requests, authentication failures and quota errors are not blindly retried. Sanitized diagnostics keep provider status and safe details without exposing the key or screenplay prompt in the error log. Raw successful responses are private project data.

Stopping a local worker prevents later local stages. It cannot recall a request already received by Gemini or guarantee that it was not billed.
