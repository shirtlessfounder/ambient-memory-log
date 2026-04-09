# AssemblyAI Room Windowed Labeling Design

**Date:** 2026-04-09

**Goal:** improve `room-1` speaker labeling quality by keeping raw room capture at `30s` chunks while delaying `room-1` publication until a longer `600s` AssemblyAI naming pass returns real roster names.

## Scope

This slice changes only the `room-1` post-capture processing path:

- keep Owl room capture at `30s`
- keep room chunk upload to S3 unchanged
- keep the existing database schema if possible
- add a room speaker roster file with richer participant descriptions
- batch `room-1` chunks into `600s` AssemblyAI processing windows
- hide `room-1` output from search until the delayed naming pass produces real names

This slice does not change:

- teammate capture
- raw audio chunk timing
- S3 upload shape
- API contract
- non-room vendor path
- historical imports

## Problem

The current room cutover to AssemblyAI is operationally working, but identity quality is still weak.

Live evidence from April 9, 2026:

- fresh `room-1` rows now land through `vendor='assemblyai'`
- the room path is no longer on `Deepgram + pyannote`
- AssemblyAI is returning diarization labels like `A`
- AssemblyAI speaker identification reported success, but the mapping was effectively `A -> A`

That means the current failure is not:

- capture
- upload
- database persistence
- worker routing

It is specifically the room identity step on short mixed-audio windows.

## Recommended Approach

Keep room capture reliable, but stop asking AssemblyAI to name speakers from isolated `30s` room chunks.

Instead:

1. continue capturing and uploading `30s` raw room chunks
2. collect contiguous `room-1` chunks into fixed `600s` worker windows
3. stitch each room window into one longer audio file
4. send that longer file plus a richer room roster to AssemblyAI
5. publish room utterances only if the result includes real roster names

This gives the identity system more context without making capture itself more fragile.

## Alternatives Considered

### 1. Keep `30s` room processing and only improve the roster text

Pros:

- smallest code change

Cons:

- live evidence already shows short room chunks are too weak for real naming
- better descriptions may help at the margin, but do not solve the lack of context

Rejected because the structural problem is insufficient room context, not just weak prompts.

### 2. Change room capture itself from `30s` to `300s` or `600s`

Pros:

- simpler processing path
- direct `raw chunk == processing unit`

Cons:

- larger capture loss on crash or USB hiccup
- makes the most failure-prone boundary more expensive
- conflicts with the preference to keep capture robust

Rejected because simplifying at the capture boundary increases loss risk.

### 3. Use adaptive `180 -> 300` or `300 -> 600` escalation

Pros:

- lower latency when short windows are sufficient

Cons:

- more branching logic
- harder to reason about
- more difficult to debug operationally

Rejected in favor of a single fixed room naming window.

## Design

### Runtime model

The room runtime shape becomes:

1. Owl records `room-1` raw audio in `30s` chunks
2. chunks upload and persist in `aa_audio_chunks` exactly as today
3. the worker keeps treating non-room sources normally
4. for `room-1`, the worker waits until it has a contiguous `600s` room window
5. the worker stitches those chunks into one temporary audio file or in-memory byte stream
6. the worker sends that stitched window plus a roster file to AssemblyAI
7. the worker publishes room utterances only when real names are returned

### Room roster input

Add a local roster file, for example:

- `config/room-speakers.json`

Recommended shape:

```json
[
  {
    "name": "Dylan",
    "description": "Male voice. Often seems to drive or steer discussion. Tends to connect product questions with systems and operational concerns, and can move across topics quickly in a single thread.",
    "aliases": ["dylan", "dylan vu"]
  },
  {
    "name": "Niyant",
    "description": "Male voice. Often comes across as analytical and technically grounded. Tends to focus on implementation details, constraints, and concrete execution tradeoffs.",
    "aliases": ["niyant"]
  },
  {
    "name": "Alex",
    "description": "Male voice. Often sounds reflective, structured, and evaluative. Tends to frame discussions around reasoning, diagnosis, and whether an approach is actually working.",
    "aliases": ["alex", "alexander janiak"]
  },
  {
    "name": "Jakub",
    "description": "Male voice. Often sounds collaborative and coordination-oriented. Tends to frame things in terms of hypotheses, alignment, community, and how people work together.",
    "aliases": ["jakub", "jakub janiak"]
  }
]
```

The worker should load this file and send the richer participant data to AssemblyAI instead of only a bare name list.

### Publish gating

Room output should no longer be published chunk-by-chunk.

Rules:

- `room-1` canonical utterances are hidden until the delayed naming pass completes
- diarization labels like `A/B/C` are never treated as human names
- a room batch is publishable only when the returned speaker names map to real roster people

This satisfies the product requirement:

- do not show unlabeled room output
- wait for the named version

### Persistence model

Preferred path without schema changes:

- keep using `aa_audio_chunks` for raw chunk tracking
- write `aa_transcript_candidates` and `aa_canonical_utterances` for `room-1` only after a room batch is accepted
- leave search/API unchanged because they already read canonical utterances

This means room data simply appears later rather than appearing twice.

If the current worker state model makes this awkward, a minimal room-only intermediate status can be added, but avoiding schema churn is preferred.

### Windowing model

Use a fixed room naming window:

- `ROOM_ASSEMBLY_WINDOW_SECONDS=600`

Optional tail behavior:

- if the room goes idle before a full `600s` window is available, allow a shorter idle flush after a configured timeout

This is the only dynamic behavior needed. No multi-stage escalation.

### Failure model

If AssemblyAI request fails:

- keep the underlying room chunks retryable
- do not publish partial room output
- retry on the next worker pass

If AssemblyAI succeeds but returns only diarization labels:

- treat that as a low-quality naming failure
- do not surface those labels as speaker names
- keep the room batch hidden from search

There should be no silent fallback to `Deepgram + pyannote` for `room-1`.

## File Map

- Create: `config/room-speakers.json`
- Create or modify: room batching helper under `src/ambient_memory/pipeline/`
- Modify: `src/ambient_memory/config.py`
- Modify: `src/ambient_memory/integrations/assemblyai_client.py`
- Modify: `src/ambient_memory/pipeline/worker.py`
- Modify: focused tests under `tests/pipeline/`
- Modify or create: focused tests for AssemblyAI client / roster parsing
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`

## Testing

### Client-level

Verify:

- richer roster payload is built correctly
- diarization-only `A/B/C` responses are recognized as unnamed
- real mapped names are parsed correctly

### Worker-level

Verify:

- `room-1` stays hidden until a full room naming window is available
- accepted named room batch persists candidates and canonical utterances
- non-room sources remain unchanged
- diarization-only room result does not publish canonical rows

### Live verification

After rollout on Dylan’s machine:

- fresh `room-1` chunks still upload every `30s`
- room searchable output appears only after roughly `10 minutes`
- searchable room rows use real names or remain hidden
- `A/B/C` no longer appear as surfaced speaker names

## Success Criteria

- room capture remains reliable at `30s`
- room search output is delayed but cleaner
- `A/B/C` are no longer surfaced as speaker names
- room labeling quality improves materially versus the current `A -> A` behavior
- non-room sources continue working as they do today

## Risks

- even `600s` windows may still be insufficient for high-confidence identity on one far-field mixed room stream
- hiding room output until naming succeeds may leave gaps when identity quality is still poor
- stitched room windows add worker complexity and increase per-batch processing time
