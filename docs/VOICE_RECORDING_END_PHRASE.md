# Recording end phrase: That's all

Opt-in, local ASR capture feature. It does not change the agent harness,
skills, IM, model, wake phrase or existing bare `stop` voice-exit command.

```yaml
voice:
  silence_duration: 5.0
  silence_threshold: 400
  end_phrase:
    enabled: true
    phrase: "That's all"
    hint_file: /absolute/path/to/cached-hint.wav
```

Uses the cached Sherpa model from `wake_word.sherpa.model_dir` (or an explicit
`voice.end_phrase.model_dir`). No automatic model download. Start a fresh CLI.
Before the first recording cue, cached audio explains: "Say that's all when
you finish speaking." The normal cue then indicates that recording is ready.

Say the full phrase **That's all**. Detection immediately ends recording;
there is no additional silence confirmation or 0.5-second waiting period.
Normal acoustic/model processing latency still applies.
An independent keyword worker reads copies of the recorder's existing PCM;
there is no second microphone or repeated full Whisper decoding. The complete
phrase is always a control command, even inside a sentence; following speech
does not cancel it. Ordinary "over the weekend" is not a match.
The final Whisper transcript removes only the terminal control phrase. A bare
end phrase produces an empty turn, not an agent task or voice-mode exit.

Fallbacks: ordinary silence after 5 seconds, existing no-speech timeout,
manual Ctrl+B and the 120-second recording cap. Decoder failure/overload leaves
silence/manual/maximum-duration paths usable. Mild background RMS <= 400
qualifies as quiet for the silence fallback; keyword stopping does not wait
for quiet. Distant/quiet speech still needs room testing.
This is an explicit command, so quoting the entire phrase at the end of a
sentence also ends recording. Do not use it as literal dictation there.

All decoding is local. Main cost is an additional small Sherpa model in CPU
memory, reused across turns, plus a bounded worker while recording. Small
PortAudio packets (e.g. 220 samples at 44.1kHz) are combined into 80ms blocks
before resampling/decoding, and heavy imports are warmed before capture.
This avoids the first-recording queue overflow missed by large-block replay.
Stop/cancel
clears worker state; shutdown releases it. Original ASR source audio retains
the spoken phrase for troubleshooting, but the transcript sent to the agent
does not. TTS should complete with recording paused, as in the existing flow.
Terminal "that's all", "that’s all", "thats all" and "that is all" are
removed from transcription; the old "over and out" suffix is also removed
for migration compatibility but is no longer an active recording-end keyword.
CLI normal and interruption submission paths repeat the same idempotent cleanup.

Rollback: set `voice.end_phrase.enabled: false`, restore your prior silence
duration/threshold, and restart the CLI. No database/history migration.

Validation must separate deterministic PCM/worker tests, real local KWS +
Whisper file replay, and human microphone/noise acceptance. Synthetic replay
does not establish multi-speaker recall or room false-positive rates.
