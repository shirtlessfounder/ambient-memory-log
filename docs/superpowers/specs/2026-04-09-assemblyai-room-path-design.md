# AssemblyAI Room Path Design

**Date:** 2026-04-09

**Goal:** replace the `room-1` meeting transcription and speaker-labeling path with `AssemblyAI`, while keeping the existing raw capture, storage, search, and non-room processing paths intact.

## Scope

This slice changes only the `room-1` processing path after raw audio capture:

- keep `Meeting Owl 3` as the live `room-1` microphone
- keep raw chunk capture and S3 upload unchanged
- replace `Deepgram + pyannote` with `AssemblyAI` for `room-1`
- keep current storage tables and search API shape
- keep non-room sources on the existing path

This slice does not change:

- raw audio capture format
- chunk timing
- search API contract
- canonical utterance storage model
- enhancement / denoise processing
- teammate and historical import pipelines

## Problem

The current `room-1` path is structurally weak for named speaker attribution:

- one far-field mixed room stream
- transcript from `Deepgram`
- identity from `pyannote`
- timestamp alignment between two separate systems
- conservative `0.75` confidence threshold before a real name is assigned

Live evidence before this slice:

- `room-1` had effectively zero useful speaker labels over recent live windows
- room transcript quality was sometimes decent gist, but named attribution was close to absent
- `desk-a` occasionally labeled because source-owner constraints helped, while `room-1` had no comparable boost

The current room path is therefore not failing mainly at storage or search. It is failing at the room-level transcript + identity layer.

## Recommended Approach

For `room-1`, use `AssemblyAI` as the unified system for:

- transcript text
- speaker-attributed utterance segmentation
- named speaker identification

Keep raw capture and downstream persistence exactly as they are today.

Why this is the right fit:

- one system owns transcript and speaker attribution together
- no more bolt-on room labeling after a separate transcript pass
- limits blast radius to the weakest path: `room-1`
- preserves current storage/search shape and rollback path
- gives the best shot at improving both transcript quality and labels without destabilizing capture

## Alternatives Considered

### 1. Keep `Deepgram + pyannote` and only lower the match threshold

Pros:

- smallest code change

Cons:

- current room confidence is far below threshold, not just slightly below it
- lowering the threshold would likely increase wrong names more than useful names
- does not address the split-system architecture

Rejected because the problem is low-confidence room identification, not just conservative gating.

### 2. Add enhancement first, keep `Deepgram + pyannote`

Pros:

- could improve transcript readability
- could reduce noise and reverberation

Cons:

- does not directly fix the fragmented transcript/identity architecture
- may distort speaker identity cues
- adds another variable before replacing the weakest system

Rejected as the first slice. Enhancement is a follow-up experiment after the new room path is in place.

### 3. Replace the whole pipeline globally

Pros:

- one vendor path everywhere

Cons:

- much larger blast radius
- unnecessary risk while only `room-1` is being actively rebuilt
- would entangle room work with non-room regressions

Rejected in favor of a room-only slice.

## Design

### Runtime model

The runtime shape stays simple:

1. `Meeting Owl 3` records `room-1` raw 30-second chunks locally
2. chunks upload to S3 and persist in `aa_audio_chunks` exactly as today
3. the worker branches by source
4. for `room-1`, the worker sends raw chunk audio to `AssemblyAI`
5. `AssemblyAI` returns transcript utterances and speaker attribution
6. the worker persists those utterances into the existing database model

Non-room sources continue using the existing `Deepgram + pyannote` path unchanged.

### Data flow

For `room-1`:

- input: raw WAV bytes from S3-backed chunk
- vendor: `AssemblyAI`
- output:
  - transcript text
  - utterance start/end times
  - speaker identifier / name
  - confidence fields when available

The worker then maps that output into:

- `aa_transcript_candidates`
- `aa_canonical_utterances`

The search layer continues reading from canonical utterances as it does now.

### Identity model

`AssemblyAI` does not reuse `pyannote` voiceprints.

Instead, the room path should provide a known participant roster:

- Dylan
- Niyant
- Alex
- Jakub

Optionally include short speaker descriptions if useful for disambiguation.

For this slice, room speaker names come directly from `AssemblyAI` output. The room path should not run pyannote matching in parallel.

### Storage model

Keep the existing tables and shapes:

- `aa_audio_chunks`
- `aa_transcript_candidates`
- `aa_canonical_utterances`
- replay audio URLs

Persist room transcript candidates with:

- `vendor='assemblyai'`
- `source_id='room-1'`

This preserves provenance and keeps downstream APIs stable.

### Config model

Add worker-only config for:

- `ASSEMBLYAI_API_KEY`

The existing `PYANNOTE_API_KEY` remains for non-room paths until a later cleanup slice.

The room path should continue using `.env.room-mic` only for capture concerns, not worker vendor selection.

### Branching model

Worker logic branches by source:

- `room-1` -> `AssemblyAI`
- everything else -> existing path

This should be explicit in code, not inferred from vendor response shape.

### Failure model

If `AssemblyAI` fails for a room chunk:

- mark the chunk failed with a visible error
- do not silently fall back to `Deepgram + pyannote` in the same run

Rollback should be operational/config-level:

- switch the room path back deliberately
- avoid mixed hidden behavior that makes debugging provenance impossible

## File Map

- Create: `src/ambient_memory/integrations/assemblyai_client.py`
- Modify: `src/ambient_memory/config.py`
- Modify: `src/ambient_memory/pipeline/worker.py`
- Modify: `tests/pipeline/test_worker.py`
- Modify: `tests/test_config.py`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`

Additional files may be needed if a shared response-normalization helper becomes appropriate.

## Testing

### Client-level

Verify:

- upload / URL handoff works
- polling completion works
- response parsing produces stable utterance objects

### Worker-level

Verify:

- `room-1` uses `AssemblyAI`
- non-room sources still use the old path
- transcript candidates persist with `vendor='assemblyai'`
- canonical utterances receive room speaker names from `AssemblyAI`

### Failure-path

Verify:

- API error marks room chunk failed
- malformed response marks room chunk failed
- no silent vendor fallback occurs

### Live verification

After rollout on Dylan’s machine:

- fresh `room-1` chunks still upload every 30 seconds
- fresh `assemblyai` transcript rows land
- a short live conversation yields materially more labeled turns than the current near-zero room baseline
- transcript quality is at least as good as the current Owl raw path

## Success Criteria

- Owl capture remains healthy
- room chunk timing remains healthy
- `room-1` transcript candidates now show `vendor='assemblyai'`
- labeled room utterances increase materially from current baseline
- storage, replay, search, and API remain stable

## Risks

- `AssemblyAI` may still underperform on a single mixed room stream
- room labeling may improve only modestly even if transcript quality improves
- vendor response shape may not map perfectly onto the current candidate/canonical model
- rollout complexity is concentrated in the worker branch logic

## Non-Goals

- enhancement / denoise in this slice
- replacing non-room pipelines
- schema redesign
- transcript “smart rewrite” or LLM repair layers
- removing pyannote from the repo entirely

## Follow-Up Slice

If `AssemblyAI` alone does not hit the quality bar, the next slice should test:

- `raw Owl audio` vs `enhanced Owl audio`
- with `AssemblyAI` on both variants

That follow-up should be treated as a separate design and implementation slice, not bundled into this one.
