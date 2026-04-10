# Room Second-Pass Enrichment Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a run-once `room-1` second-pass enrichment flow that writes inferred speaker/text improvements into a separate table for the most recent `4h`, without mutating canonical utterance raw fields or row shape.

**Architecture:** Add a new enrichment persistence model plus Alembic migration, then introduce a bounded enrichment pipeline that reads recent `aa_canonical_utterances` in fixed `15-minute` windows, runs a two-pass resolver client, and writes one enrichment row per canonical utterance and resolver version. Expose it via a top-level CLI command with dry-run support, and keep all new LLM wiring isolated under `src/ambient_memory/integrations/`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, Typer, stdlib HTTP transport, pytest.

---

## Chunk 1: Schema And Test Seams

### Task 1: Add failing schema and model tests

**Files:**
- Modify: `tests/db/test_models.py`
- Create: `tests/pipeline/test_room_enrichment.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing table metadata coverage**

Add assertions that `Base.metadata.tables` contains `aa_canonical_utterance_enrichments`, the table includes the required columns, and a uniqueness strategy exists for `(canonical_utterance_id, resolver_vendor, resolver_version)`.

- [ ] **Step 2: Run the schema test and verify it fails**

Run: `uv run pytest tests/db/test_models.py -q`
Expected: FAIL because the enrichment table/model does not exist yet.

- [ ] **Step 3: Write failing enrichment pipeline tests**

Add tests covering:
- enrichment rows persist separately from canonical rows
- rerunning with the same resolver version stays idempotent
- canonical `text`, `speaker_name`, `started_at`, and `ended_at` remain unchanged
- row-preserving behavior across a `15-minute` window
- explicit `unknown` speaker outputs
- recent `4h` scope excludes older utterances
- dry-run reports windows/utterances and performs no writes

- [ ] **Step 4: Run the new pipeline/CLI tests and verify they fail**

Run: `uv run pytest tests/pipeline/test_room_enrichment.py tests/test_cli.py -q`
Expected: FAIL because the enrichment pipeline and CLI command do not exist yet.

## Chunk 2: Implementation

### Task 2: Add schema, config, and isolated resolver client

**Files:**
- Modify: `src/ambient_memory/models.py`
- Create: `migrations/versions/20260410_0004_add_canonical_utterance_enrichments.py`
- Modify: `src/ambient_memory/config.py`
- Create: `src/ambient_memory/integrations/openai_room_enrichment_client.py`

- [ ] **Step 1: Add the failing migration/model support**

Define `CanonicalUtteranceEnrichment` with the required columns, relationship, and uniqueness/index strategy.

- [ ] **Step 2: Implement the Alembic migration**

Create `aa_canonical_utterance_enrichments`, foreign key it to `aa_canonical_utterances.id`, and add the unique constraint/index used for idempotent reruns.

- [ ] **Step 3: Add minimal settings support**

Expose the resolver key/model/version fields needed by the run-once enrichment path, following existing `BaseSettings` patterns and without disturbing worker settings.

- [ ] **Step 4: Implement the isolated resolver client**

Add an OpenAI-backed client with two methods:
- `resolve_speakers(window)` returning only allowed names `Dylan|Niyant|Alex|Jakub|unknown`
- `cleanup_text(window)` returning per-utterance cleaned text plus confidence/notes

Keep request/response validation strict and row-preserving.

- [ ] **Step 5: Run targeted tests**

Run: `uv run pytest tests/db/test_models.py tests/integrations/test_openai_room_enrichment_client.py -q`
Expected: PASS once model + client land.

### Task 3: Add the enrichment pipeline and CLI

**Files:**
- Create: `src/ambient_memory/pipeline/room_enrichment.py`
- Modify: `src/ambient_memory/cli.py`
- Modify: `.env.example`
- Modify: `docs/ops-machine-setup.md`
- Modify: `README.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Implement query + windowing**

Load only `room-1` canonical utterances within `now() - hours`, grouped into fixed `15-minute` windows, ordered deterministically.

- [ ] **Step 2: Implement two-pass enrichment persistence**

For each window:
- pass 1 speaker resolution
- pass 2 cleanup using raw text + context + resolved speaker
- upsert-or-skip per `(canonical_utterance_id, resolver_vendor, resolver_version)`

- [ ] **Step 3: Wire the operator CLI**

Add `ambient-memory enrich-room` with:
- `--hours`
- `--source-id` default `room-1`
- `--resolver-version`
- `--dry-run`

Dry-run must report utterance/window counts and avoid writes.

- [ ] **Step 4: Update operator docs/env examples**

Document the resolver env var, CLI command, recent-`4h` scope, and rerun semantics.

- [ ] **Step 5: Run targeted pipeline/CLI/docs tests**

Run: `uv run pytest tests/pipeline/test_room_enrichment.py tests/test_cli.py tests/test_docs.py -q`
Expected: PASS.

## Chunk 3: Verification

### Task 4: Full verification, migration, and live run

**Files:**
- No new files expected

- [ ] **Step 1: Run the broader relevant suite**

Run: `uv run pytest tests/db/test_models.py tests/integrations/test_assemblyai_client.py tests/pipeline/test_worker.py tests/pipeline/test_room_enrichment.py tests/test_cli.py tests/test_docs.py -q`
Expected: PASS.

- [ ] **Step 2: Apply the migration locally**

Run: `uv run alembic upgrade head`
Expected: migration applies cleanly to the configured local database.

- [ ] **Step 3: Capture live before-state aggregates**

Run SQL or a helper script to record:
- recent `4h` canonical utterance count for `room-1`
- current raw speaker label distribution
- existing enrichment row count for the selected resolver version

- [ ] **Step 4: Run the real bounded enrichment**

Run: `uv run ambient-memory enrich-room --hours 4 --source-id room-1 --resolver-version <version>`
Expected: enrichment rows written only for eligible recent `room-1` canonical utterances.

- [ ] **Step 5: Capture live after-state verification**

Collect:
- before/after aggregate counts
- raw vs enriched sample rows
- proof canonical raw fields stayed unchanged
- exact rerun command for the same `4h` window

- [ ] **Step 6: Commit the isolated change**

Use a conventional commit after tests and live verification are complete.

