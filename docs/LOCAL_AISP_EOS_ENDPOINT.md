# Native AISP EOS recording endpoint

Based on accepted `docker-20261014.1` with initialization warmup retained.
Enable `voice.endpoint: {enabled: true, provider: aisp-eos}` and disable the
legacy `voice.end_phrase`. The ops EOS image migrates only these two keys on
init/start with a private rollback snapshot; existing profile settings remain.

The bridge exports the installed SDK AISP_FLAG_EOS and forwards timestamp/flag
callbacks even when output_text is absent. The service stops a recording at the
first EOS bit for speech in the currently armed post-cue segment. It never uses
"thank you" as a trigger. Empty EOS after a partial is accepted; stale/no-speech
and duplicate events are ignored. Wake keyword detection is unchanged.

Recording clients negotiate `endpoint: aisp-eos`; the server echoes it at ready.
End events retain `end_recording` and recording_id with reason `aisp_eos`.
An older service without the capability fails the handshake instead of silently
using its old keyword. Existing silence, no-speech timeout, max duration and
manual stop remain available on failure. No new capture stream or model exists.

The recorder submits once via its existing callback, reports "Speech ended",
shows automatic stopping, and leaves control-looking words in the transcript.
No additional quiet window or ASR/VAD tuning is added. Generic legacy keyword
support remains for non-EOS configurations. Warmup and scene reset are retained.

Tests: canonical runner with tests/tools/test_aisp_eos.py and existing voice
endpoint, cue, warmup, scene and prompt restoration suites. New tests include
real Unix socket handshakes, sequential recordings, cancellation and EOS flags.
`demo-runtime/wake/check_eos.py` checks the compiled SDK constant and service gate
without models/audio. Physical pauses, final-word retention, EOS timing and
microphone/speaker behavior require human acceptance of the exact candidate pair.
