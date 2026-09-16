# Voice latency review and stage-one implementation

## Evidence (laptop, 2026-09-16)

Session `20260916_210244_17318d` compared typed "hi how are you" with
spoken "How are you?". Typed input used one model call (~2.0 s).
Voice recording stopped at 21:03:54.821; transcription completed 21:03:59.524;
ack finished 21:04:01.094; agent finished 21:04:06.892; final audio was
generated 21:04:08.946. Voice main inference itself took ~2.1 s.

Voice used routing, native answer and summarization (three model calls).
The natural 29-word, three-sentence answer was summarized because `_brief`
requires at most two sentences. The final result used third-person narration.
This is a confirmed postprocessing defect, not evidence that the main model
cannot answer a greeting naturally.

Prompt layers: shared `general_voice.VOICE_EXECUTION_CONTRACT` (also present
for typed CLI), continuity router instructions, current-turn execution context,
and summary instructions. The legacy CLI voice prefix appears in api_content
but the buffered continuation strips it at the wire boundary. Buffered turns
disable streaming and override temperature. These layers remain unchanged here.

## Stage one only

1. Remove model-thread waiting for turn-start ack playback; keep FIFO speech,
   one ack, cancellation/scene ownership and microphone echo protection.
2. Measure complete ASR latency (consume segments), distinguishing load, audio
   preparation/VAD and decode. Evaluate Intel GPU via isolated OpenVINO first,
   then existing-model CPU beams 5/3/1 and threads, then smaller English models.
3. Do not change routing, answer/summary policy, final TTS or mail ownership.

Acceptance gates: GPU ASR median >=25% faster, P95 no worse; concurrent OVMS
P95 TTFT and throughput degradation <=10%; no audio dropout or memory failure.
CPU candidates need >=20% median improvement and no P95 regression. Quality:
WER increase <=1 percentage point and no new errors in critical names/numbers/
intent or silence hallucinations. No representative labeled human recordings
means quality is provisional, not grounds to replace the production backend.

Current ASR is English-only, prewarmed and resident, with fixed beam=5 and
cross-window conditioning already disabled. CTranslate2 is CPU/CUDA, not an
Intel GPU backend. OpenVINO requires a separate runtime/model export; do not
point faster-whisper at device="GPU" or modify the live OVMS container.

Development and deployment use task worktrees, reviewed GitHub commits, exact
releases and private ops receipts. Models/audio/secrets stay outside Git.
No active CLI/OVMS restart, no Box rollout. Experiment results and actual backend
selection are recorded in the ops handoff. Routing/summary optimization is a
separate later discussion.

## Implemented boundaries

`cli.py` queues the existing fixed acknowledgement and a playback marker,
then starts `run_conversation` without waiting for audio. Only the microphone
monitor waits in `hermes_cli/voice_ack.py`. Session id, scene generation, queue
ownership, cancellation and clarify/approval states prevent a stale marker
from reopening capture. The shared TTS FIFO still owns ack and final audio;
missing cache generation stays on its synthesis worker. A failed ack does not
block inference. Initial wake greeting and end-phrase introduction are unchanged.

`tools/transcription_tools.py` records load/config, frontend and fully consumed
decode timings without transcript text. `stt.local.beam_size` accepts integer
1..10, defaults to the existing 5, and invalid values fall back to 5. VAD,
English language selection and confidence safeguards remain unchanged.

`scripts/local-ovms/benchmark-asr.py` runs against existing local model folders
and mono PCM16/16 kHz WAVs, reporting cold and warm runs separately. Use
`--backend faster-whisper --device cpu --beams 5 3 1` for the current engine;
use an isolated OpenVINO environment with `--backend openvino --device GPU`
for the GPU experiment. `--manifest` accepts a JSON array of `{path, reference}`
objects; transcript output is opt-in via `--show-text`. Synthetic clips are
performance diagnostics only, not a representative human accuracy benchmark.
OpenVINO is currently an experimental inference path without the production
VAD wrapper, not a selectable deployment backend. A separate resident provider
is warranted only after the performance, accuracy and OVMS coexistence gates.

Source map for the deferred prompt work: `hermes_cli/general_voice.py` owns
the shared execution contract and brief-answer predicate;
`hermes_cli/voice_continuity_router.py` owns the extra routing prompt;
`hermes_cli/voice_continuity.py` owns buffered-turn integration. Review the
actual outgoing wire messages rather than assuming stored `api_content`
exactly equals model input. No change to these policies is in this release.
