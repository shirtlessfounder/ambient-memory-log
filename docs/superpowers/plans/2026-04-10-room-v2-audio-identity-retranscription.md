# Room V2 Audio Identity And Retranscription Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current text-only `room-1` enrichment path with a bounded v2 run-once flow that derives speaker identity from per-track audio verification and derives inferred transcript text from a second-pass audio transcription, while keeping the raw Assembly room rows untouched.

**Architecture:** Extend the existing `aa_canonical_utterance_enrichments` table with room-v2 audit fields, then refactor `room_enrichment.py` to load recent `room-1` canonical utterances plus provenance, reconstruct per-window/per-track audio from S3-backed room chunks, resolve each Assembly diarization track through a pluggable audio identity verifier, retranscribe the full window audio through an audio transcription client, align that inferred transcript back onto the existing utterance timeline, and persist one inferred row per canonical utterance and resolver version. Keep the CLI entrypoint stable as `ambient-memory enrich-room`, but change the implementation behind it to room-v2 and expose the new thresholds/models through `RoomEnrichmentSettings`.

**Tech Stack:** Python 3.14, SQLAlchemy 2.x, Alembic, Typer, boto3 S3 client reuse, pyannote voiceprint verification, OpenAI audio transcription API, ffmpeg/wave helpers, pytest.

---

## File Map

- Modify: `src/ambient_memory/models.py`
- Create: `migrations/versions/20260410_0005_extend_room_enrichments_for_v2.py`
- Modify: `src/ambient_memory/config.py`
- Create: `src/ambient_memory/pipeline/room_track_audio.py`
- Create: `src/ambient_memory/pipeline/room_track_identity.py`
- Create: `src/ambient_memory/pipeline/room_transcript_alignment.py`
- Create: `src/ambient_memory/integrations/openai_room_retranscription_client.py`
- Modify: `src/ambient_memory/pipeline/room_enrichment.py`
- Modify: `src/ambient_memory/cli.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/db/test_models.py`
- Create: `tests/pipeline/test_room_track_audio.py`
- Create: `tests/pipeline/test_room_track_identity.py`
- Create: `tests/pipeline/test_room_transcript_alignment.py`
- Create: `tests/integrations/test_openai_room_retranscription_client.py`
- Modify: `tests/pipeline/test_room_enrichment.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_docs.py`

## Chunk 1: Schema, Settings, And Failing Seams

### Task 1: Add failing model, config, and orchestration tests for room v2

**Files:**
- Modify: `tests/db/test_models.py`
- Modify: `tests/pipeline/test_room_enrichment.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing schema expectations**

Add coverage proving `aa_canonical_utterance_enrichments` now exposes room-v2 audit columns:
- `identity_method`
- `identity_track_label`
- `identity_window_started_at`
- `identity_match_label`
- `identity_match_confidence`
- `identity_second_match_label`
- `identity_second_match_confidence`
- `transcript_method`
- `transcript_confidence`

Keep the existing uniqueness strategy on `(canonical_utterance_id, resolver_vendor, resolver_version)`.

- [ ] **Step 2: Write the failing room-enrichment expectations**

Extend `tests/pipeline/test_room_enrichment.py` so it proves:
- room v2 still writes one enrichment row per canonical utterance
- raw canonical `text`, `speaker_name`, `started_at`, and `ended_at` remain unchanged
- one diarization track maps to one inferred identity within a `15m` window
- low-speech tracks become `unknown`
- unmatched non-teammate tracks can become `external-1`
- idempotent rerun behavior still holds for the same resolver version

- [ ] **Step 3: Write the failing CLI expectations**

Update CLI tests so `ambient-memory enrich-room` expects the room-v2 defaults:
- same command name
- default `resolver_version` changed to a room-v2 label such as `room-v2-audio-identity-v1`
- same `--hours`, `--source-id`, `--resolver-version`, `--dry-run` flags

- [ ] **Step 4: Run the focused failing tests**

Run: `uv run pytest tests/db/test_models.py tests/pipeline/test_room_enrichment.py tests/test_cli.py -q`

Expected: FAIL because the schema fields and room-v2 orchestration do not exist yet.

### Task 2: Add failing settings coverage for the new room-v2 knobs

**Files:**
- Modify: `tests/test_worker_config.py`
- Modify: `src/ambient_memory/config.py`

- [ ] **Step 1: Write the failing config tests**

Add tests proving `RoomEnrichmentSettings` exposes:
- `AWS_REGION`
- `PYANNOTE_API_KEY`
- `OPENAI_AUDIO_TRANSCRIBE_MODEL`
- `ROOM_TRACK_MIN_SPEECH_SECONDS`
- `ROOM_TRACK_MATCH_THRESHOLD`
- `ROOM_TRACK_MATCH_MARGIN`

Use exact defaults:

```python
openai_audio_transcribe_model = "gpt-4o-transcribe-diarize"
room_track_min_speech_seconds = 8.0
room_track_match_threshold = 0.75
room_track_match_margin = 0.15
```

- [ ] **Step 2: Run the focused config tests and verify failure**

Run: `uv run pytest tests/test_worker_config.py -q -k enrichment`

Expected: FAIL because the new room-v2 settings are not defined yet.

- [ ] **Step 3: Implement the minimal settings support**

In `src/ambient_memory/config.py`, extend `RoomEnrichmentSettings` with:

```python
aws_region: str = Field(alias="AWS_REGION")
pyannote_api_key: str = Field(alias="PYANNOTE_API_KEY")
openai_audio_transcribe_model: str = Field(default="gpt-4o-transcribe-diarize", alias="OPENAI_AUDIO_TRANSCRIBE_MODEL")
room_track_min_speech_seconds: float = Field(default=8.0, alias="ROOM_TRACK_MIN_SPEECH_SECONDS", gt=0)
room_track_match_threshold: float = Field(default=0.75, alias="ROOM_TRACK_MATCH_THRESHOLD", ge=0, le=1)
room_track_match_margin: float = Field(default=0.15, alias="ROOM_TRACK_MATCH_MARGIN", ge=0, le=1)
```

- [ ] **Step 4: Re-run the config tests**

Run: `uv run pytest tests/test_worker_config.py -q -k enrichment`

Expected: PASS.

## Chunk 2: Extend Enrichment Storage

### Task 3: Add the room-v2 enrichment migration and model fields

**Files:**
- Modify: `src/ambient_memory/models.py`
- Create: `migrations/versions/20260410_0005_extend_room_enrichments_for_v2.py`
- Modify: `tests/db/test_models.py`

- [ ] **Step 1: Add the failing migration expectation**

Extend schema tests to assert the new columns exist on `CanonicalUtteranceEnrichment`.

- [ ] **Step 2: Implement the SQLAlchemy model extension**

Add nullable fields to `CanonicalUtteranceEnrichment`:

```python
identity_method: Mapped[str | None] = mapped_column(String(50))
identity_track_label: Mapped[str | None] = mapped_column(String(50))
identity_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
identity_match_label: Mapped[str | None] = mapped_column(String(100))
identity_match_confidence: Mapped[float | None] = mapped_column(Float)
identity_second_match_label: Mapped[str | None] = mapped_column(String(100))
identity_second_match_confidence: Mapped[float | None] = mapped_column(Float)
transcript_method: Mapped[str | None] = mapped_column(String(50))
transcript_confidence: Mapped[float | None] = mapped_column(Float)
```

Keep `cleaned_text` as the inferred text column to minimize churn.

- [ ] **Step 3: Implement the Alembic migration**

Create `20260410_0005_extend_room_enrichments_for_v2.py` that adds those fields without changing existing rows or uniqueness constraints.

- [ ] **Step 4: Run the focused schema tests**

Run: `uv run pytest tests/db/test_models.py -q`

Expected: PASS.

## Chunk 3: Room Provenance And Track Audio Reconstruction

### Task 4: Add failing tests for provenance loading and track bundle reconstruction

**Files:**
- Create: `tests/pipeline/test_room_track_audio.py`
- Create: `src/ambient_memory/pipeline/room_track_audio.py`

- [ ] **Step 1: Write the failing tests**

Cover:
- loading only `room-1` provenance rows from `aa_utterance_sources -> aa_transcript_candidates -> aa_audio_chunks`
- preferring the `UtteranceSource.is_canonical` room candidate when present
- ignoring non-room provenance even if canonical utterance has mixed-source provenance
- grouping utterances by `speaker_hint` / raw track label `A/B/C/D`
- slicing WAV bytes by utterance-relative timestamps
- stitching one audio bundle per track
- computing pooled speech seconds per track with `measure_speech_seconds`

Use fake in-memory S3 byte maps and small generated WAV fixtures.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `uv run pytest tests/pipeline/test_room_track_audio.py -q`

Expected: FAIL because the track-audio helper does not exist yet.

- [ ] **Step 3: Implement the provenance/audio helper**

Create `src/ambient_memory/pipeline/room_track_audio.py` with focused dataclasses such as:
- `RoomProvenanceSlice`
- `RoomTrackBundle`
- `RoomWindowAudio`

Implement helpers to:
- query room provenance rows for a window
- fetch chunk bytes via an injected S3 client
- slice WAV bytes by time range
- stitch full-window audio and per-track audio bundles
- compute per-track speech seconds using `ambient_memory.pipeline.room_speech.measure_speech_seconds`

Reuse the existing worker S3 client builder rather than inventing a new fetch mechanism.

- [ ] **Step 4: Re-run the focused track-audio tests**

Run: `uv run pytest tests/pipeline/test_room_track_audio.py -q`

Expected: PASS.

## Chunk 4: Track-Level Audio Identity

### Task 5: Add failing tests for stable track identity assignment

**Files:**
- Create: `tests/pipeline/test_room_track_identity.py`
- Create: `src/ambient_memory/pipeline/room_track_identity.py`
- Reuse: `tests/integrations/test_pyannote_client.py`

- [ ] **Step 1: Write the failing identity tests**

Cover:
- teammate assignment only when top pyannote match clears threshold and margin
- low-speech tracks become `unknown`
- unmatched but speechful non-teammate track becomes `external-1`
- additional unmatched tracks after `external-1` become `unknown`
- all utterances on the same raw track inherit the same inferred identity
- audit fields capture top and second-best matches

- [ ] **Step 2: Run the focused identity tests and verify failure**

Run: `uv run pytest tests/pipeline/test_room_track_identity.py -q`

Expected: FAIL because the track-identity resolver does not exist yet.

- [ ] **Step 3: Implement the identity resolver**

Create `src/ambient_memory/pipeline/room_track_identity.py` with:
- `ResolvedTrackIdentity`
- `resolve_track_identities(...)`

Implementation rules:
- input is a tuple of `RoomTrackBundle`
- call `PyannoteClient.identify_speakers(...)` once per track bundle
- inspect each returned `IdentificationMatch.confidence` map
- compute top and second-best teammate matches
- apply:
  - `threshold >= 0.75`
  - `margin >= 0.15`
  - `pooled speech seconds >= 8.0`
- output one identity per track:
  - teammate name
  - `external-1`
  - `unknown`

Do not reuse the old segment-level `choose_speaker(...)` logic directly; the unit of decision here is the whole track bundle, not a short mixed segment.

- [ ] **Step 4: Re-run the focused identity tests**

Run: `uv run pytest tests/pipeline/test_room_track_identity.py -q`

Expected: PASS.

## Chunk 5: Audio Retranscription And Timeline Alignment

### Task 6: Add failing tests for second-pass window transcription and alignment

**Files:**
- Create: `tests/integrations/test_openai_room_retranscription_client.py`
- Create: `tests/pipeline/test_room_transcript_alignment.py`
- Create: `src/ambient_memory/integrations/openai_room_retranscription_client.py`
- Create: `src/ambient_memory/pipeline/room_transcript_alignment.py`

- [ ] **Step 1: Write the failing retranscription client tests**

Cover:
- OpenAI audio transcription request shape for `gpt-4o-transcribe-diarize`
- strict response parsing into time-bounded segments
- transport failures and malformed JSON / malformed segment payload handling

- [ ] **Step 2: Write the failing alignment tests**

Cover:
- align inferred transcription segments back onto the existing canonical utterance timeline by maximal time overlap
- preserve one output text per canonical utterance
- fall back to raw canonical text when no inferred segment aligns cleanly
- preserve ordering

- [ ] **Step 3: Run the focused tests and verify failure**

Run: `uv run pytest tests/integrations/test_openai_room_retranscription_client.py tests/pipeline/test_room_transcript_alignment.py -q`

Expected: FAIL because the audio retranscription client and alignment helper do not exist yet.

- [ ] **Step 4: Implement the OpenAI audio retranscription client**

Create `src/ambient_memory/integrations/openai_room_retranscription_client.py` with:
- stdlib multipart upload helper
- `transcribe_window(...)` method
- strict parsing into dataclasses like:

```python
RoomRetranscribedSegment(
    start_seconds: float,
    end_seconds: float,
    text: str,
    confidence: float | None,
)
```

Use `gpt-4o-transcribe-diarize` by default from settings.

- [ ] **Step 5: Implement the alignment helper**

Create `src/ambient_memory/pipeline/room_transcript_alignment.py` that maps retranscribed segments back to the existing canonical utterances in a row-preserving way.

- [ ] **Step 6: Re-run the focused retranscription/alignment tests**

Run: `uv run pytest tests/integrations/test_openai_room_retranscription_client.py tests/pipeline/test_room_transcript_alignment.py -q`

Expected: PASS.

## Chunk 6: Refactor The Room Enrichment Orchestrator

### Task 7: Replace the text-only room enrichment flow with room v2 orchestration

**Files:**
- Modify: `src/ambient_memory/pipeline/room_enrichment.py`
- Modify: `tests/pipeline/test_room_enrichment.py`

- [ ] **Step 1: Add the failing end-to-end orchestration tests**

Update `tests/pipeline/test_room_enrichment.py` so the main run proves:
- it loads recent `room-1` canonical utterances as before
- it reconstructs one audio window and per-track bundles
- it resolves one inferred identity per track
- it retranscribes the full window audio
- it aligns inferred text back onto existing utterance ids
- it writes extended enrichment rows with audit fields
- reruns stay idempotent

- [ ] **Step 2: Run the focused orchestration tests and verify failure**

Run: `uv run pytest tests/pipeline/test_room_enrichment.py -q`

Expected: FAIL because `room_enrichment.py` still uses the old text-only resolver contract.

- [ ] **Step 3: Refactor `room_enrichment.py`**

Refactor the module to:
- keep the recent-hour and fixed-window selection logic
- swap the resolver abstraction from text-only per-utterance methods to room-v2 services:
  - provenance/audio reconstruction
  - track identity
  - full-window retranscription
  - timeline alignment
- continue writing exactly one enrichment row per canonical utterance and resolver version

Set the new default resolver version constant to:

```python
DEFAULT_ROOM_ENRICHMENT_RESOLVER_VERSION = "room-v2-audio-identity-v1"
```

- [ ] **Step 4: Re-run the focused orchestration tests**

Run: `uv run pytest tests/pipeline/test_room_enrichment.py -q`

Expected: PASS.

## Chunk 7: CLI, Docs, And Operator Surface

### Task 8: Keep the CLI stable but update it for room v2

**Files:**
- Modify: `src/ambient_memory/cli.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Update the CLI wiring**

Keep the command name:

```bash
uv run ambient-memory enrich-room --hours 4 --source-id room-1 --resolver-version room-v2-audio-identity-v1
```

Update help text and defaults to describe:
- audio-track identity
- audio-aware retranscription
- same-version idempotency
- raw canonical rows remain unchanged

- [ ] **Step 2: Update env/docs**

Document the new required envs for room v2:
- `OPENAI_API_KEY`
- `OPENAI_AUDIO_TRANSCRIBE_MODEL`
- `PYANNOTE_API_KEY`
- `AWS_REGION`
- `ROOM_TRACK_MIN_SPEECH_SECONDS`
- `ROOM_TRACK_MATCH_THRESHOLD`
- `ROOM_TRACK_MATCH_MARGIN`

Clarify that:
- raw room rows stay as Assembly output
- enrichment rows are the inferred layer
- v1 text-only relabeling is superseded by v2 for room evaluation

- [ ] **Step 3: Run the focused CLI/docs tests**

Run: `uv run pytest tests/test_cli.py tests/test_docs.py -q`

Expected: PASS.

## Chunk 8: Full Verification And Live 4h Evaluation

### Task 9: Run the migration, dry-run, live run, and quality checks

**Files:**
- No new files expected

- [ ] **Step 1: Run the broader relevant suite**

Run:

```bash
uv run pytest \
  tests/db/test_models.py \
  tests/integrations/test_pyannote_client.py \
  tests/integrations/test_openai_room_retranscription_client.py \
  tests/pipeline/test_room_track_audio.py \
  tests/pipeline/test_room_track_identity.py \
  tests/pipeline/test_room_transcript_alignment.py \
  tests/pipeline/test_room_enrichment.py \
  tests/test_cli.py \
  tests/test_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Apply the migration locally**

Run: `uv run alembic upgrade head`

Expected: PASS.

- [ ] **Step 3: Capture before-state aggregates**

Record:
- recent `4h` room canonical count
- raw fallback label distribution
- existing enrichment row count for `room-v2-audio-identity-v1`

- [ ] **Step 4: Run dry-run first**

Run:

```bash
uv run ambient-memory enrich-room --hours 4 --source-id room-1 --resolver-version room-v2-audio-identity-v1 --dry-run
```

Expected: reports windows and utterances without writing rows.

- [ ] **Step 5: Run the bounded live v2 enrichment**

Run:

```bash
uv run ambient-memory enrich-room --hours 4 --source-id room-1 --resolver-version room-v2-audio-identity-v1
```

Expected: writes one v2 enrichment row per eligible recent `room-1` canonical utterance.

- [ ] **Step 6: Capture after-state quality checks**

Collect:
- inferred speaker distribution
- `% unknown`
- count of rows with `external-1`
- count of rows where inferred text differs from raw text
- proof raw canonical rows stayed unchanged
- joined samples showing:
  - raw fallback label
  - inferred identity
  - inferred transcript text
  - audit fields for top/second match

- [ ] **Step 7: Check for catastrophic failures**

Run targeted SQL or helper scripts to flag:
- raw track mapping to multiple inferred identities within the same `15m` window
- teammate names assigned with weak confidence below threshold
- missing inferred rows for eligible recent room utterances

If those fail materially, stop and report instead of widening rollout.

- [ ] **Step 8: Commit the isolated change**

Use a conventional commit only after tests, migration, and live verification are complete.
