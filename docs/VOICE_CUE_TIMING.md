# Wake cue and capture timing

The normal wake acknowledgement is followed by a 50ms fixed pause (previously
150ms), then the existing 880Hz, 120ms recording cue. The first combined
endpoint-instruction pause remains 650ms. User system volume and beep_volume
are respected: the recording quiet cap is acquired only after the cue and
capture start, so it no longer attenuates the cue itself.

AudioRecorder.prepare_capture opens/reuses the input stream and prepares the
endpoint before acknowledgement playback. Idle input is discarded, with no
pre-cue audio retained. Manual recording also prepares the stream before its
cue. capture_clock records the PortAudio clock immediately after the cue;
start accepts an optional capture_after boundary on the same clock. Queued
blocks crossing the boundary are trimmed by sample instead of discarded whole.
PCM collection is enabled before EOS handshake and before the quiet lease RPC,
so both operations can finish while the command prefix is already collected.
No earlier speech is reconstructed; the ready cue ends the protected boundary.
The existing native recording EOS old-segment policy remains unchanged.

recording_start(after_cue=True) defers quiet acquisition to the caller and
retains failure cleanup; legacy decorator behavior stays available. Recording
end, cancel, and scene cleanup continue to restore the existing quiet lease.
Termux recorders without preparation/clock support keep their existing capture
path. Persistent streams remain open and discard input between recordings.

Tests drive the actual recorder callback and CLI start method, including cue
samples, a crossing first block, audio received during endpoint/quiet calls,
and the generated WAV bytes. Software timing checks do not certify acoustic
quality: validate by saying one, two, three immediately after the cue and
checking microphone audio and the transcript separately. Early speech during
the greeting or cue is intentionally outside this change.
