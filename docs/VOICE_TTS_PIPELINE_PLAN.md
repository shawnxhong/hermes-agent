# Voice TTS pipeline implementation plan

Approved 2026-09-16: laptop only, two independently reversible stages. No
routing, summary eligibility, prompts, mail policy, IM or iGPU ASR changes.

1. Resident CPU Kokoro: same model, af_maple and speed. Private Unix socket;
   prewarm, one model and serial synthesis. Lightweight command client retains
   WAV/output interface. One legacy fallback on unavailability, never retry
   cancellation. Existing Hermes player and fixed-ack caches remain owners.
2. Optional per-turn sentence delivery: stream the existing JSON summary call,
   extract only decoded summary content, validate complete sentences before
   committing them. Native direct answers still wait for finalization; do not
   expose tool preambles or raw model deltas. Reuse bounded synthesis/playback
   lookahead; prevent duplicate final enqueue. Delivery status comes only from
   the host after its outcome. No new model calls or validators.

User accepts irreversible early first-sentence delivery: a later failure must
not replay a full fallback. Before any commit use the existing fallback;
after a commit preserve the prefix and append one brief failure notice if
needed. Record segment sequence/status without logging content by default.
Cancellation and scene generation invalidate every old pending audio item.
Keep microphone barriers until complete speech, not merely the first sentence.

Tests: JSON escapes/boundaries, abbreviations/decimals, incomplete/invalid
summary, failure before/after commit, queue ordering, cancellation at every
stage, missing cache/service, keyboard/IM isolation and real local pipeline.
Benchmark cold and >=20 warm runs, median/P95. Targets: >=30% warm synthesis
improvement and >=20% first-audio improvement on multi-sentence responses,
no P95 regression; OVMS/ASR concurrent P95 regression <=10%. Tune CPU threads
before considering other devices. Synthetic measurements do not replace
human voice/prosody/interrupt acceptance.

Publish each stage through task worktree, tests, reviewed PR, exact laptop
release and private ops receipt. Never restart the user's live CLI. Rollback
sentence streaming independently, then legacy command TTS if necessary.

Stage 1 measurement: 117-character fixture, 20 warm syntheses, af_maple.
Legacy command median/P95 1.771/1.950 s; resident 8-thread no-spin socket
1.092/1.153 s (client process overhead excluded from the latter).
Eight interleaved coexistence pairs: ASR P95 0.298 -> 0.311 s; OVMS TTFT
P95 0.313 -> 0.266 s and throughput median 42.19 -> 41.51 tokens/s.
Default ORT spin significantly hurt coexistence; explicitly disable both
intra/inter-op spinning. These synthetic probes do not certify acoustics.
