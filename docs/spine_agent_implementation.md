# Spine Health Agent implementation notes

## Implemented first-release loop

`authenticated account → default/private case → adult questionnaire → deterministic triage → local evidence lookup → controlled Agent action → explicit confirmation → job/review record`

The implementation is intentionally case-centred. Existing `personal_case_entries` are not migrated destructively: the new workspace shows them as a legacy timeline while new state lives in `mindspine_spine_agent.sqlite3`.

## Public API

All endpoints require the existing account cookie.

- `GET/POST /api/cases` lists or creates case ledgers.
- `GET /api/cases/default/workspace` creates the current user's default case once and returns the Agent workspace.
- `GET /api/cases/{case_id}/timeline` returns legacy entries alongside new assessment/review state.
- `POST /api/cases/{case_id}/questionnaire` stores the versioned adult intake and deterministic triage result.
- `PUT /api/cases/{case_id}/consent` controls whether a de-identified structured summary may be sent to DeepSeek for wording only.
- `POST /api/cases/{case_id}/agent/messages` executes the constrained agent graph.
- `POST /api/agent-actions/{action_id}/confirm` consumes the one-time confirmation token.
- `POST /api/cases/{case_id}/members` grants a scoped, expiring collaboration role.

The Agent response contains a user-facing message, evidence cards, result cards, proposed actions and a safety status. It does not return hidden chain-of-thought.

## Deterministic safety and ReAct policy

The graph is: `case access → triage → evidence retrieval → at most one allowlisted proposal → local validation → response`.

- Urgent/soon questionnaire results use maximum rule severity. They suppress automatic rehabilitation and propose only a review task.
- Actions have `draft`, `confirmed`, `queued`, `running`, `completed`, `needs_review`, `failed` or `cancelled` state. A confirmation token is hashed at rest and expires after 15 minutes.
- Disabled third-party tools never create invented output. Confirmation creates an explicitly non-diagnostic assessment record in `needs_review` and an open review task.
- Capture eligibility is the minimum of capture, landmark and protocol quality. Trend comparison rejects different model/protocol/unit/capture conditions and any low-quality pair.

## RAG and content governance

There are three boundaries: structured case facts, clinical knowledge, and technical/reproduction knowledge. Case facts are not put into the shared knowledge corpus.

The ingestion API accepts heading-aware sections. Guideline prose is chunked at 400–700 Chinese characters with a small overlap. Tables, questionnaire items and exercise definitions are atomic chunks; they cannot be split away from contraindications or stopping rules. Metadata stores audience, topic, source URL, review state, usage policy and reviewer.

The current runtime has a local lexical baseline and an isolated semantic-worker slot for BGE-M3 + Qdrant. Unreviewed WHO/NICE references are registered as `reference_pending_clinical_review`; they cannot create individual rehabilitation advice. Every patient-facing medical claim must have either an enabled evidence record or a structured case fact. Otherwise the Agent abstains.

## DeepSeek boundary

DeepSeek receives only `case_scope`, triage level, non-identifying symptom scores and assessment states after explicit consent. It receives no names, case IDs, free-text notes, original images, video, photos or DICOM. The model performs a schema-constrained `return_safe_copy` call for wording only. Local code rejects unsafe output and retains the deterministic response on any provider error.

Legacy `deepseek-chat`/`deepseek-reasoner` configuration is migrated in memory to `deepseek-v4-flash`; operator configuration files are not modified.

## Third-party reproduction policy

See `third_party_eval_20260828/reproduction_manifest.json` and `reproduction_runs/2026-08-28-core-algorithm-run.json`. RepDetect's 33-landmark embedding/KNN/counter and OpenCap-style calibrated triangulation quality gate have passed local synthetic tests, but their product tools remain disabled. The full Android/ML Kit and OpenSim Workers, TotalSegmentator/MONAI and surface reconstruction remain isolated adapters until their fixed versions, dependencies, synthetic/de-identified fixtures, cancellation handling, output provenance and regression tests pass. A consumer photo surface archive is explicitly non-diagnostic and cannot be geometrically overlaid with an X-ray.

## Clinical activation required

The database supports an approved adult exercise catalog. Each item must include eligibility, contraindications, stopping conditions, dose bounds, a version and a reviewer. Only then can a confirmed `save_rehab_plan` action create a catalog-constrained plan. The repository ships with no clinically approved exercise item, so the first release correctly routes rehabilitation requests to review instead of fabricating a treatment plan.
