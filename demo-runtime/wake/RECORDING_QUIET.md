# Recording quiet lease

The mounted wake YAML owns `recording_quiet`: enabled by default, fan control
30%, speaker ceiling20%, thermal release80C, hard limit120s. Fan/output have
independent enabled flags. Only `asr.hotwords` biases ASR; wake aliases remain
separate. Deployment preserves all existing ASR/language/VAD/audio overrides.

The CLI acquires a host lease after wake acknowledgement finishes, before the
recording cue/capture, including manual recording. The greeting retains normal
output volume. Native EOS is
already recording-ID scoped: the wake phrase's EOS cannot release this lease.
After recorder.stop(), release precedes the end cue, transcription and LLM/TTS.
Startup errors, cancelled wake acknowledgements, voice off and scene changes
release too. A client heartbeat renews every1s; host expiry is5s with a120s hard
limit that renewals cannot extend. Old tokens cannot release a newer recording.
The protocol is local Unix JSON acquire/renew/release; it exposes no model tool.

The user host service reads the same YAML on each acquisition, caps the effective
default output (preserving stereo balance and mute), and tracks manual changes
and output switches. Crash recovery only restores a volume still matching the
service's last write. The narrow root fan helper has no shell/path operations in
its protocol and accepts only the configured host UID. Its writable mounts are
one selected fan-state file and its private state/socket directory; no privileged
container or Docker socket. Thermal readings and fan RPM are read-only. A worker
serializes slow ACPI writes independently of lease expiry and audio handling.
Fan loss/thermal override does not prevent volume limiting or ASR capture.

Fan control percentage is a cooling-state request, not an RPM guarantee. On White
Box the initial30% probe wrote state15/50 but RPM stayed around2645: physical
speed reduction remains unverified. Logs deliberately say effect=unverified.
Do not disable thermald or claim fan noise reduction from cur_state alone.

Host services must be installed together with the CLI and wake images. Config
changes require restarting wake for ASR fields; recording_quiet itself is read
on the next lease. Host/service failure never blocks ordinary recording. See the
ops handoff for pinned deployment, restore state, tests and rollback commands.
