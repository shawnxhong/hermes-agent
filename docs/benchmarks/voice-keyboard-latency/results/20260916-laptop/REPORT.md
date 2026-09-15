# Keyboard vs Voice end-to-end latency benchmark

## Result status

- Recorded: **32/32** runs; transport-complete: **32**; runtime errors: **0**.
- Keyboard mean: **68.978 s**; voice mean: **92.816 s**; mean voice penalty: **23.838 s** (**1.346x** keyboard).
- Keyboard median: **75.862 s**; voice median: **72.852 s**.
- Median voice penalty: **-3.01 s** (**0.96x** keyboard).
- Source under test: `/home/agentdemo/.hermes/hermes-agent` at `d63f996a757f6255fc1454239616ab4b4435e0f5`; host: `laptop`.

Transport-complete means the harness returned; it does not mean the answer was useful.  Manual content review is summarized below and recorded case-by-case in `quality-review.json`.
The lower voice median is not evidence that voice is faster: several voice runs returned short failure or wrong-task answers, while the 387.788-second schedule outlier raises the mean and matches the reported long-tail experience.

| mode | pass | partial | fail | unreviewed |
|---|---|---|---|---|
| keyboard | 9 | 2 | 5 | 0 |
| voice | 5 | 2 | 9 | 0 |

## Method

Two long-lived CLI sessions (one per mode) processed the same 16 English prompts in the same order.  Mode execution order alternated per case.  Voice timing starts when a completed WAV is submitted to local ASR and ends after final audio playback.  It includes ASR, the configured turn-start acknowledgement, model/tool work, voice routing/summarization, final TTS synthesis and playback.  Keyboard timing starts at text submission and ends when the complete answer is returned.

Input WAVs were synthesized before measurement and then transcribed by the configured local Whisper path.  Playback used the real generated audio duration but slept silently instead of driving the speaker.  Wake-word detection, user speaking time, microphone capture and VAD/end-phrase waiting are intentionally outside the end-of-utterance metric.  Web queries were live.  Home, cron, files, sessions and voice outbox were isolated; SMTP workers were disabled.

Both modes used Hermes' existing headless single-query clarify callback.  If the model calls `clarify`, the callback immediately tells it that no user is available and to make a reasonable assumption.  This keeps every listed case a deterministic one-prompt measurement; it does not include a human follow-up delay.

## Overall end-to-end latency (seconds)

| mode | runs | mean | median | p95 | min | max |
|---|---|---|---|---|---|---|
| keyboard | 16 | 68.978 | 75.862 | 133.375 | 6.497 | 139.715 |
| voice | 16 | 92.816 | 72.852 | 163.073 | 19.071 | 387.788 |

## Scenario comparison (mean seconds)

| scenario | keyboard | voice | voice - keyboard |
|---|---|---|---|
| casual_chat | 47.105 | 44.809 | -2.296 |
| flight_search | 78.655 | 100.898 | 22.243 |
| home_appliance_control | 104.941 | 40.073 | -64.868 |
| knowledge_qa | 57.236 | 58.624 | 1.388 |
| online_shopping | 48.157 | 62.86 | 14.703 |
| schedule_management | 29.687 | 275.43 | 245.743 |
| simple_coding | 72.364 | 82.442 | 10.078 |
| travel_planning | 113.681 | 77.394 | -36.287 |

## Voice critical-path bottlenecks, largest first

These four mutually exclusive stages sum to voice end-to-end time apart from sub-millisecond timestamp rounding.

1. **agent/model/tool turn** — 64.744 s/run, 69.755% of mean voice E2E.
2. **post-agent final TTS/render** — 20.265 s/run, 21.834% of mean voice E2E.
3. **ASR after utterance** — 6.255 s/run, 6.739% of mean voice E2E.
4. **turn-start acknowledgement/pre-agent** — 1.553 s/run, 1.673% of mean voice E2E.

| rank | critical-path stage | mean/run | total |
|---|---|---|---|
| 1 | agent/model/tool turn | 64.744 | 1035.906 |
| 2 | post-agent final TTS/render | 20.265 | 324.235 |
| 3 | ASR after utterance | 6.255 | 100.072 |
| 4 | turn-start acknowledgement/pre-agent | 1.553 | 24.849 |

## Voice component work, largest first

This table is diagnostic work attribution.  Tool, synthesis and playback work can overlap other stages and therefore must not be summed as a second end-to-end total.

| rank | component work | mean/run | total |
|---|---|---|---|
| 1 | main streamed LLM calls | 56.481 | 903.7 |
| 2 | ack and final audio playback | 15.619 | 249.899 |
| 3 | voice router/summary auxiliary LLM calls | 7.914 | 126.624 |
| 4 | TTS synthesis work (may overlap playback) | 5.933 | 94.93 |
| 5 | tool execution work (may overlap) | 1.303 | 20.846 |

The auxiliary LLM row includes voice continuity/task routing, title work and spoken summarization, but excludes non-stream native-agent calls that are already present in the main LLM row.

## ASR and first audible feedback

- ASR returned a non-empty transcript for **16/16** voice cases.
- Mean/median word error rate: **0.071 / 0.048**; maximum: **0.25**.
- The turn-start acknowledgement began after **6.291 s mean / 5.398 s median** and played for **1.513 s mean**.
- The final answer audio began after **78.692 s mean / 55.555 s median**; final playback itself averaged **14.106 s**.
- Case-level `time_to_ack_audio_s`, `asr_word_error_rate`, ack playback and final playback are in `summary.csv`.

## Slowest voice cases

| case | scenario | E2E | agent turn | ASR | post-agent |
|---|---|---|---|---|---|
| schedule_02 | schedule_management | 387.788 | 350.247 | 8.487 | 27.529 |
| schedule_01 | schedule_management | 163.073 | 134.577 | 7.491 | 19.48 |
| flights_01 | flight_search | 109.281 | 66.883 | 11.123 | 29.743 |
| flights_02 | flight_search | 92.515 | 64.297 | 8.724 | 17.967 |
| travel_01 | travel_planning | 92.088 | 77.506 | 4.922 | 7.702 |
| coding_02 | simple_coding | 83.781 | 36.755 | 4.398 | 41.104 |
| coding_01 | simple_coding | 81.103 | 46.165 | 8.648 | 24.762 |
| knowledge_01 | knowledge_qa | 75.158 | 50.706 | 5.271 | 17.656 |

## Interpretation constraints

- The benchmark measures the deployed local model and live network conditions at one point in time; search latency and results are variable.
- Synthetic input makes ASR timing reproducible, but does not measure microphone quality, VAD silence duration, wake-word recall or a human speaker's recognition accuracy.
- Silent equal-duration playback avoids audible test output while preserving audio duration; sound-device startup/driver latency is not represented.
- The two mode sessions intentionally accumulate the same scenario sequence, matching the target long-lived demo session and exposing context-growth costs.
- Captured email deliverables prove harness behavior and content only.  No real SMTP delivery or receipt wait is included.
- Another idle Hermes CLI may remain open; no service or user process was stopped.  Any overlapping external use of OVMS would appear as real contention in these measurements.

See `summary.csv`, `outputs.md`, `raw-results.json`, and `environment.json` in this directory for case-level evidence.
