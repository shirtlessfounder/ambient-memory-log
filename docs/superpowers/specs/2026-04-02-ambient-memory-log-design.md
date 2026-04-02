# Ambient Memory Log Design

Date: 2026-04-02
Status: Approved for planning
Owner: Dylan/team

## Summary

Build a recall-first ambient conversation capture system for a 4-person team working in a 500-600 sq ft living room. The system should run from 9am ET to 12am ET, capture desk talk, self-talk, roaming conversations, whiteboard discussions, and small all-hands conversations, then store raw audio in AWS and produce a searchable canonical transcript log.

The recommended MVP is a hybrid capture topology:

- 4 local capture agents, one on each MacBook, using the built-in microphone
- 1 central room capture box with 1-2 hidden wired room microphones for roam zones
- raw audio stored in Amazon S3 forever
- delayed processing in 30-60 second windows for better speaker labeling and dedup
- hosted speech-to-text for transcription
- pyannote voiceprints for persistent speaker identity
- internal merge/dedup logic to produce one canonical conversation log

This is primarily a systems-integration project, not a model-training project.

## Goals

- Capture as much speech as possible from the 4-person team
- Preserve raw audio for replay and future reprocessing
- Produce a canonical transcript log with timestamps and named speakers
- Make transcripts searchable and indexable for downstream AI-agent memory use
- Keep user friction low
- Keep prototype hardware roughly in the $200-$300 range where possible

## Non-Goals

- Perfect attribution during heavy overlap
- Courtroom-grade transcription accuracy
- Zero duplicates in the first prototype
- Full enterprise conference-room hardware in the MVP
- Agent retrieval, summarization, or downstream memory ranking in this phase

## Constraints

- Net-new project
- Team of 4, all with separate MacBooks
- Work happens in a shared living room
- Two people are often at desks
- Two people move around the room
- Team sometimes shifts to a whiteboard or bean bags
- No wearable microphones, lavs, or headsets as the primary solution
- Low-friction setup is more important than perfect accuracy
- Recall is the highest priority failure mode: missing important speech is worse than wrong attribution or noisy duplicates
- One-time speaker enrollment of 2-5 minutes per person is acceptable
- Processing can be delayed up to about 1 minute for higher quality
- Third-party hosted APIs are acceptable if they simplify the system

## Problem Framing

This problem is not "pick the best speech model." It is:

- capture topology
- source synchronization
- speaker identity across sessions
- transcript merge/dedup
- searchable storage

Raw ASR quality leaderboards are useful inputs but insufficient for system selection. Artificial Analysis is a good source for raw transcription comparisons, but it does not answer ambient capture, persistent identity, or dedup architecture decisions.

References:

- Artificial Analysis speech-to-text leaderboard: <https://artificialanalysis.ai/speech-to-text>
- Deepgram multichannel vs diarization: <https://developers.deepgram.com/docs/multichannel-vs-diarization>
- AssemblyAI streaming diarization and multichannel: <https://www.assemblyai.com/docs/streaming/diarization-and-multichannel>
- pyannote voiceprints tutorial: <https://docs.pyannote.ai/tutorials/identification-with-voiceprints>

## Recommended Approach

### Option Chosen

Use a hybrid capture system:

- near-field local capture from each team member's MacBook
- ambient room capture from a central box with hidden room microphones
- separate transcription per source
- delayed merge and dedup into a canonical event stream

### Why This Option

- Room-only capture is too weak on speaker attribution for a moving, overlap-heavy living room.
- Personal wearable microphones would likely give the best attribution, but they violate the friction constraint.
- Hybrid capture preserves the strongest source of recall: nearby laptop audio for desk/self-talk plus room coverage for roaming areas.
- Duplicate capture is acceptable because the system can clean it up later. Missing audio is harder to recover.

## Architecture

### Capture Layer

There are five capture classes in the MVP:

- MacBook A local microphone
- MacBook B local microphone
- MacBook C local microphone
- MacBook D local microphone
- central room source with 1-2 hidden wired microphones

Each source runs rolling local chunk capture. A source emits:

- compressed audio file
- source metadata
- wall-clock start/end timestamps
- device health and upload status

The central room box should be treated as just another source from the backend's point of view.

### Storage Layer

Use two persistence surfaces:

- Amazon S3 for raw audio blobs
- AWS database for structured transcript records and metadata

The database stores:

- canonical utterance text
- utterance timestamps
- speaker name and confidence
- source evidence/provenance
- dedup relationships
- processing version
- pointers to raw audio in S3

The database should not store audio blobs directly.

### Processing Layer

Processing runs in delayed windows, likely every 30-60 seconds:

1. detect newly uploaded chunks
2. group them by time window
3. transcribe each source independently
4. run speaker matching and speaker naming
5. merge near-duplicate utterances
6. persist one canonical log

This is intentionally not a hard realtime pipeline. Quality is prioritized over immediate display.

### Component Boundaries

The MVP should be split into clear units with narrow interfaces:

- capture agent
  - records local audio
  - writes chunks to S3
  - emits chunk metadata and health status
- ingest coordinator
  - receives new-chunk events
  - groups chunks into processing windows
  - schedules downstream work
- transcription worker
  - fetches source audio
  - calls the hosted STT vendor
  - emits timestamped segment candidates
- speaker matching worker
  - scores segment-to-person matches using enrolled voiceprints
  - emits named speaker candidates plus confidence
- merge/dedup worker
  - reconciles competing source transcripts
  - emits one canonical utterance record plus provenance
- transcript store and search index
  - persists the canonical log
  - supports transcript search and replay lookup

Each unit should communicate through durable records or queued jobs, not in-memory coupling.

## Audio Topology

### MacBook Capture

Each MacBook agent captures its built-in microphone continuously during active hours.

Strengths:

- strongest self-talk coverage
- strongest desk-talk coverage
- natural bias toward the owner when that person speaks near the device
- no new user friction

Weaknesses:

- far weaker for roam-zone conversations
- bleed from nearby speakers
- dependent on users keeping the laptop nearby and open

### Room Capture

Add 1-2 hidden wired room microphones connected to one always-on central capture machine.

Target areas:

- whiteboard zone
- bean bag / roaming collaboration zone

Strengths:

- covers speech that no laptop catches well
- improves recall during movement
- creates backup evidence when local capture is weak

Weaknesses:

- far-field audio lowers transcription quality
- speaker attribution is harder
- overlap remains difficult

### Why Not Bluetooth As Core Capture

Bluetooth microphone paths on Mac are not the preferred primary architecture. Apple documents quality tradeoffs when the Bluetooth microphone is active, and a stable wired prototype is simpler.

Reference:

- Apple Bluetooth audio quality note: <https://support.apple.com/en-lamr/102217>

## Vendor Strategy

### Speech-to-Text

Use a hosted speech-to-text vendor for the MVP. The main job of the vendor is transcription plus timestamped segmentation. The system should avoid binding too much product logic to one vendor's diarization output.

Recommended initial bake-off:

- Deepgram first
- AssemblyAI second
- optional third candidate from the current Artificial Analysis leaders if implementation effort is reasonable

Reasoning:

- Deepgram's documentation is clear about the difference between multichannel separation and diarization, which matches the merge-oriented architecture.
- AssemblyAI has strong streaming features, but its own docs explicitly describe overlap, short-turn, and noisy-environment limitations for diarization. That does not disqualify it, but it means ambient room diarization alone should not be treated as sufficient identity logic.

### Speaker Identity

Use pyannote voiceprints to map speech segments to the 4 known users after one-time enrollment. This should be treated as a separate identity layer rather than assuming diarization labels alone are enough.

Identity heuristics:

- if the segment comes from a user's MacBook and voiceprint confidence agrees, strongly bias to that user
- if the segment comes from a room mic, rely more on voiceprint matching
- keep confidence scores and allow uncertain attribution

### Storage

Use Amazon S3 directly for raw audio retention. Keep audio forever from day 1 if that simplifies the prototype. Add lifecycle/archival later if needed.

## Processing Pipeline

### 1. Capture

Each source records rolling chunks locally.

Recommended local chunk size:

- 30 seconds for capture/upload reliability

Each chunk should include:

- source_id
- source_type
- device_owner if applicable
- start_time
- end_time
- checksum
- storage path

### 2. Ingest

When a chunk lands in S3, enqueue it for processing. The backend groups chunks into overlapping windows, likely 30-60 seconds, to allow better merge and speaker inference.

### 3. Transcription

Run transcription on each source independently.

Do not mix all sources into one audio file before transcription. Mixing destroys source provenance and makes downstream dedup and attribution materially harder.

Output per segment should include:

- transcript text
- start/end times
- word timings if available
- vendor confidence
- vendor diarization label if available

### 4. Speaker Matching

Use the enrolled voiceprints to score candidate speaker identity for each segment.

For local MacBook sources:

- prior on device owner
- adjust by voiceprint confidence

For room sources:

- rely more on voiceprint confidence
- degrade confidence for short or noisy segments

### 5. Merge and Dedup

This is the core internal system logic.

Multiple devices may capture the same utterance. The merge layer should compare segments using:

- overlapping timestamps
- transcript similarity
- speaker compatibility
- source quality tier
- confidence scores

Output:

- one canonical utterance
- alternate supporting segments retained as provenance

Preference order when conflicts exist:

1. local near-field source with higher confidence
2. room source as fallback or support evidence
3. unresolved ambiguity stored with lower speaker confidence

### 6. Canonical Log

Persist a single merged event stream with:

- utterance_id
- canonical text
- start_time
- end_time
- speaker_name
- speaker_confidence
- canonical_source_id
- alternate_source_ids
- raw_audio_pointers
- processing_version

### 7. Replay and Reprocessing

The system must support:

- playing back source audio from transcript rows
- retranscribing old audio with a new vendor or model
- recomputing speaker identity or dedup as algorithms improve

This is why raw audio is stored permanently.

## Failure Handling

The MVP must tolerate long-running ambient capture without manual babysitting.

Minimum reliability behaviors:

- if upload fails locally, the capture agent retries and keeps a bounded local backlog
- if a device is offline, missing chunks are visible via source health metrics
- if transcription fails for a chunk, the chunk is retried without losing the raw audio
- if speaker identity is uncertain, store the segment with low confidence instead of forcing a wrong name
- if merge/dedup cannot confidently reconcile two candidates, keep one canonical record and retain conflicting evidence for later reprocessing
- if clocks drift across devices, preserve both device-local timestamps and server receive time so sync can be corrected later

## MVP Scope

### In Scope

- local MacBook capture agents
- central room capture source
- raw audio uploads to S3
- transcription via hosted API
- one-time voice enrollment for 4 users
- speaker naming with confidence
- delayed merge/dedup
- canonical searchable transcript store

### Out of Scope

- polished live transcript UI
- advanced summaries/action items
- downstream AI-agent memory retrieval strategy
- mobile apps
- custom model training
- fine-grained admin policy tooling

## Success Criteria

The MVP is successful if:

- the team consistently finds important speech in the transcript log
- desk and self-talk capture is clearly useful
- roaming/whiteboard capture is imperfect but still materially useful
- named speakers are mostly correct in non-overlap segments
- the team trusts the canonical log enough to search it

## Known Risks

- overlap-heavy speech remains hard for all vendors
- far-field room audio may still miss words
- timestamp drift between devices can hurt dedup quality
- wrong speaker assignment may look plausible if confidence is not surfaced
- room mics may increase duplicates significantly until merge logic matures

## Prototype Budget Assumption

The first prototype should assume:

- existing MacBooks are reused
- the central capture box is reused hardware if possible
- 1-2 cheap wired room microphones are added
- software/API spend is acceptable if it shortens time to signal

This is a prototype to determine whether ambient hybrid capture yields useful searchable memory, not a production conference-room deployment.

## Open Decisions For Planning

- exact local capture implementation per MacBook
- exact central capture hardware
- exact STT vendor chosen after bake-off
- transcript database schema
- canonical search/index backend
- correction workflow for wrong speaker attribution
- observability and ops model for long-running daily capture

## Recommended Next Step

Write an implementation plan focused on:

- capture agents
- S3 ingest path
- transcription worker
- voice enrollment and speaker matching
- merge/dedup engine
- searchable transcript store

## References

- Artificial Analysis speech-to-text leaderboard: <https://artificialanalysis.ai/speech-to-text>
- Deepgram multichannel vs diarization: <https://developers.deepgram.com/docs/multichannel-vs-diarization>
- AssemblyAI streaming diarization and multichannel: <https://www.assemblyai.com/docs/streaming/diarization-and-multichannel>
- pyannote voiceprints tutorial: <https://docs.pyannote.ai/tutorials/identification-with-voiceprints>
- Amazon S3 lifecycle management: <https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html>
- Amazon S3 storage classes: <https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html>
- Amazon S3 pricing: <https://aws.amazon.com/s3/pricing/>
- Apple Bluetooth audio note: <https://support.apple.com/en-lamr/102217>
