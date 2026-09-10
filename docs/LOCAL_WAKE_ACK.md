# Local wake acknowledgement

For the screenless US demo, a recognized `Hi Intel` now has an independent
spoken cue: **Hi, I'm here.** This is not the later task acknowledgement
(**Sure, let me check.**) and uses no LLM, search, memory, email, or permission
prompt. The configured local Kokoro TTS supplies audio.

```yaml
voice:
  wake_ack:
    enabled: true
    text: "Hi, I'm here."
```

Use `text: "我在。"` for a Chinese demonstration. Before the user speaks there
is no input language to detect, so this cue uses the configured language rather
than guessing. Default behavior without this opt-in remains unchanged.

Order: detector match → pause detector → preserve/select session → play cached
cue → 150 ms speaker-tail guard → existing recording beep and microphone start.
The watchdog stays busy until capture starts, so it cannot resume detection
while the cue is synthesized or played. Duplicate busy wakes are ignored.
Stop/exit, a changed session, wake-off or queued input cancels pending capture.
TTS failure logs a warning and leaves the existing recording/beep path usable.

Only the CLI wake callback invokes this module. Typed/IM messages, ordinary
question follow-up capture and per-task acknowledgement are unchanged. A fresh
wake within the existing 120-second continuation window still keeps context.

Audio is cached under `$HERMES_HOME/cache/wake-ack/`, keyed by text and TTS
configuration. Complete audio is atomically published; changed text/voice
generates a new entry. No credential values are written into cache filenames.
This host has pre-generated both suggested cues: English 1.40 seconds, Chinese
0.87 seconds; repeated cache lookup was about 0.1 ms. No cloud TTS is introduced.

## Voice launcher readiness

`stt.enabled` controls transcription provider availability; it does not enable
the interactive CLI's in-memory voice state. Therefore a launch could show an
armed wake listener while Ctrl+B remained inert until the first successful wake.
`hermes-mode voice --run` now sets a process-local auto-start flag so the CLI
enters voice mode at its initial prompt. Ordinary `hermes --cli` behavior is
unchanged.

The wake listener initializes asynchronously and, on this demo host, can take
about 1.2 seconds after CLI startup to open the microphone. The voice launcher
now requests a one-time double high ready tone. Immediately before the tone the
CLI pauses the newly opened stream; immediately after the tone and a 250 ms
speaker-tail guard it re-opens the stream and waits for the listener's real
ready event. Speak the wake phrase only after this tone. This startup cue does
not reuse or replay the follow-up instruction speech.

When Ctrl+B starts manual capture, the CLI pauses an active local wake listener
before opening the recorder. In the demo launcher Ctrl+B is a one-shot fallback:
after that turn's TTS, the watchdog resumes wake detection instead of starting
another recording. An ordinary agent response that explicitly ends with a
question still uses the separate bounded follow-up module to play its ready cue
and open exactly one answer window. This prevents both microphone contention and
the old unconditional post-answer ASR loop.

The demo launcher also sets `wake_word.sensitivity: 0.30` (previously 0.45)
and `wake_word.confirmation_frames: 2`. For the configured sherpa provider,
the lower sensitivity maps to a more permissive keyword threshold.

Validation: native CLI wake callback order, real WAV cache contract, disable,
failure and cancellation paths, plus existing wake, follow-up, travel, email,
TTS and plugin regressions. Actual local Kokoro generation and speaker playback
were also checked without opening the microphone. Room acoustics and perceived
cue timing still need the user's manual wake test.

Restart the interactive CLI (`hermes-voice`) after deployment. The configuration
is opt-in and profile-aware; no gateway behavior or global toolset was changed.
