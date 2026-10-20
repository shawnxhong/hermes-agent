# TTS wake buffer

For external-ASR interactive wake sessions, voice.wake_buffer_seconds defaults
to 1.0; 0 disables it and positive finite values through 2 seconds are accepted.
This retains a keyword recognized during the last second of a spoken response.
It never interrupts TTS or records audio while playback is active.

The sidecar retains at most 64 keyword metadata records in RAM for five seconds.
The timestamp is the first recognition arrival on the host monotonic clock,
not the SDK sentence-start timestamp. Repeated partials cannot extend it.
After successful local file-based TTS drains, the CLI sends the actual final
playback time and response-scoped window on its next wake subscription. A ready
handshake precedes any cached wake delivery. Normal live wake then continues.

Completed-TTS replay bypasses the ordinary three-idle-poll delay, but still
requires the previous agent turn, recording and transcription to be idle.
Cancellation, scene/session changes, replacement turns, queued typed input and
expired completions discard replay. The native recording EOS and old-segment
guards are unchanged. PCM-streaming providers without local file-completion
metadata keep their ordinary wake behavior; the accepted Kokoro path is covered.

To reduce self-triggering with AEC disabled, cached aliases present in the last
1024 characters of the spoken reply are excluded. This is conservative: if
Hermes is itself explaining a wake phrase, repeat it after playback ends.
No replay speech text is logged or stored on disk. Existing ASR logs retain
their established behavior.

Tests exercise the actual TTS consumer, native Unix socket handshake and CLI
watchdog with synthetic audio and controlled keyword callbacks. Human timing
and acoustic acceptance are separate; ASR decode latency is not removed by a
one-second keyword buffer.
