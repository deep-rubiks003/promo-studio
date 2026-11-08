# Promo Studio local implementation plan

Latest change implemented: local number/logline display without AI; no genre prerequisite; one or multiple promos with one draft per episode/custom group; optional Spoiler checker after drafting. This replaces historical three-variation requirements below. workflow.py implements schema_version2 grouping, durable partial generation and scoped spoiler findings. POST /api/projects/{id}/spoiler-check starts an explicit check. Existing projects stay stored.

Goal: A runnable Windows app that reads real timestamped TXT screenplays, understands only user-selected episodes with Gemini, creates three source-grounded editorial variations per opportunity, supports reviewed prompt changes, and exports verified cuts.

Latest user correction: Import may contain all episodes, but no automatic full-pack sending. Choose episodes BEFORE analysis. Trial defaults to episodes 1–3; analysis and promo calls use only chosen source. Generation outside the analyzed subset requires fresh selected-episode understanding. This supersedes earlier full-pack-analysis statements below.

Architecture: FastAPI serves a vanilla HTML/CSS/JS studio and JSON API on 127.0.0.1:8787. SQLite persists projects, jobs, and untouched provider responses. The API key stays in server memory. No authentication or public hosting in this local version. All writes require the local session token and same-origin requests. Model is configurable; default gemini-3.8-flash, never silently replaced.

Scope: 20–60 second continuous or stitched cuts; source evidence; five-criterion weighted editorial score (not viral probability); full-dialogue boundary checks; explicit limitations of script timestamps; proposed refinements applied only after validation; full-pack genre analysis but selected-source-only generation. No video editing, dashboard metrics, fictional results, or optional feature bundle.

Visual direction: preserve the approved charcoal/apricot Promo Studio, four steps Import / Understand / Direct / Create, compact script-centered editor. Add private connection dialog, saved project picker, honest job progress, errors and empty states.

## Shared contract

`parser.py`: parse_episode(filename: str, text: str) -> episode dict; validate_cut(variation: dict, episodes: list, selected_ids: list, minimum=20, maximum=60, structure='continuous') -> dict.
Episode: {id, number, filename, title, logline, duration, warnings:[], events:[{id,start,end,speaker,kind,text}]}; kind dialogue/action/camera/sound. IDs immutable. parse failures ValueError.
Variation input: {id, angle, distinction, segments:[{episode_id,start,end}], beats:[{role,event_ids:[],reason}], question, reason, weakness, rubric:[{criterion,score,reason}], narrative:{standalone,escalation,cliffhanger,coherent}, review_notes}. Rubric criteria Hook / Standalone clarity / Escalation / Cliffhanger / Cut quality. Roles Hook / Context / Escalation / Cliffhanger.
Validation output: {eligible,duration,checks:[{name,passed,detail}],warnings:[],evidence:[{role,reason,events:[]}],score}. Exact evidence comes from parsed events, never model-written quotations.

HTTP API returns JSON; errors {detail:string}. GET /api/status -> {configured,model,budget,spent,token,projects:[{id,name,updated_at}]}. Every POST includes X-Studio-Token from status.
POST /api/connect {key,model?,budget:2} -> status (no key echoed).
POST /api/projects {name,files:[{name,text}]} -> project.
GET /api/projects/{id} -> project {id,name,episodes,analysis:null|{title,logline,primary_genre,secondary_genre,themes:[],relationships:[],evidence:[],warnings:[]},brief:null|dict,groups:[],proposals:{},updated_at}.
POST /api/projects/{id}/analyze {} -> {job_id}.
POST /api/projects/{id}/generate {selected_ids,group_count:1..3,minimum:20,maximum:60,structure:'continuous'|'stitched',emphasis,primary_genre,secondary_genre} -> {job_id}.
POST /api/projects/{id}/refine {variation_id,prompt,part:'Whole variation'|'Hook'|'Context'|'Escalation'|'Cliffhanger'} -> {job_id}.
GET /api/jobs/{id} -> {id,status:'queued'|'running'|'done'|'error',message,error?,project_id}.
POST /api/projects/{id}/apply {variation_id} -> project.
POST /api/projects/{id}/discard {variation_id} -> project.
GET /api/projects/{id}/export -> attachment JSON with eligible ranked variations, failures, source provenance, raw model outputs, methodology. No private key. All groups export together.
Group: {id,title,opportunity,variations:[variation with validation]}. Proposal in project.proposals[variation_id] = {variation,change,tradeoff}.
Calls are serialized. Progress comes from real stage changes; no fabricated percent. Failed tasks preserve previous results. Re-running generation replaces results only on success. Clear confirmation in UI before overwrite.

## Tasks and checks
- [ ] Parser: tests first for native timestamp format and simple timestamp lines, overlapping dialogue, incomplete continued turns, finite times, selected scope, duration, evidence containment and score gates. Run all 50 supplied files.
- [ ] Frontend: retain established design; wire real upload, connection, projects, jobs, selection, generation, evidence/score, refinement and export. Render all user/model strings as text, no unsafe HTML.
- [ ] Server/provider: persistence, local security, Gemini structured output, full-pack chunk analysis, bounded candidate discovery, cut review based only on clip, budget reservations and actual token accounting, retry-safe error handling, raw outputs.
- [ ] Integration: mocked provider tests for failures and atomic writes; browser flow using real upload and mocked deterministic server tests separately; verify key privacy and no false success. Live Gemini test within user-approved $2 only after private key entry.
- [ ] Handoff: Windows launch script, requirements, README, source ZIP excluding private data; bounded desktop/mobile review and design record.

Ruling: user already approved product workflow and explicitly authorized build; local-first, timestamped TXT and ready key settle the remaining implementation choices. Proceed without another design approval gate.
Ruling: build in a new outputs folder, preserving previous prototypes. No publishing or git history changes.
