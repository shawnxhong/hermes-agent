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

Stage 1 actual command-client measurement including interpreter startup:
20 warm calls, median/P95 1.126/1.184 s (36% median improvement).

## Stage 2 implementation and rollout

`voice.sentence_pipeline.enabled: true` selects the per-turn CLI sink. Absent
or false preserves buffered final speech, independently of resident Kokoro.
Only a real local voice turn with a TTS queue creates a sink. Context-local
ownership prevents keyboard/IM/subagent use; queue/session/scene checks and
the existing stop event invalidate stale audio. Existing microphone barriers
still wait for all speech. No change to prompts, budget, summary eligibility,
native model callbacks, result body, mail policy or iGPU ASR.

The existing final continuity-summary call uses the same JSON schema and
prompt with stream=true. The first complete decoded sentence is validated
against the existing spoken-summary policy and formatting before enqueue.
Only this first sentence commits early. The rest waits for complete JSON,
normal finish reason and full validation, then final enqueue omits the prefix.
Direct native answers are sentence-split only after task completion.
Escapes, decimals, common abbreviations and chunk-tail ambiguity are buffered.
No timer forces partial speech. Post-commit failure appends one brief notice
instead of replay; cancellation is silent. Segment id/sequence/state/timing
is logged without content. Synthesizing/ready are distinct from played/failed.

Existing single-synthesis-worker/bounded-lookahead/single-player pipeline is
reused. No second TTS player, new model request, or cloud backend is added.
An in-flight CPU synthesis may finish after cancellation but stale audio is
discarded before playback. Resident-client cancellation never starts fallback.

Real OVMS -> summary parser -> configured resident TTS -> validated WAV
probe in a temporary Hermes profile, 20 warm interleaved pairs: buffered
summary-to-first-WAV median/P95 3.216/3.294 s; sentence delivery 1.976/2.011 s
(39% median improvement). Playback was replaced by a WAV-validation/timestamp
sink: no speaker sound or mail/IM sent. This measures first-audio readiness,
not total ASR-to-answer latency or acoustic quality. Human acceptance remains.
