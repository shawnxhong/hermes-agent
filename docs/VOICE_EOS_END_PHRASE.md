# Native EOS plus an explicit recording end phrase

The wake ASR YAML owns the recording end phrase:

```yaml
end_phrase:
  enabled: true
  phrase: thank you
```

Both native AISP EOS and this explicit phrase can end the same recording.
The first successful notification wins; repeated partials or a later EOS do
not stop it twice. The prepared CLI recognizes the configured phrase from this
recording's own PCM without waiting for the SDK EOS flag. Mentioning the complete
phrase during recording is itself a control signal. The configuration is shared
with the native sidecar; there is no second CLI phrase setting. Legacy clients
use the sidecar's complete word-boundary matching in live ASR partials instead.

New YAML sections default to enabled thank you. Existing YAML without the
section receives the same defaults. Set enabled to false to retain native EOS
alone; the phrase remains editable. No ASR hint, VAD threshold or AEC change is
made. Restart the wake service and reconnect the CLI after editing, while idle.

End phrases are recording-scoped; they do not wake the agent or stop playback.
Pre-cue phrases are never replayed. Current PCM detection excludes all samples
before the cue. Legacy shared-ASR detection accepts only a newly appended phrase
in a known, unchanged old prefix; unknown/revised old prefixes are ignored.
Native EOS keeps its stricter post-cue sentence policy in both cases.

The ready message advertises the active sidecar setting. The client accepts
end_recording reason aisp_eos for its current recording ID, and the prepared
recorder generates end_phrase locally; legacy clients accept either socket reason.
The client preserves the actual winning reason. The native endpoint name remains aisp-eos for
compatibility. CLI recording hints use the advertised phrase. Transcription
cleanup is unchanged; the control phrase can remain in the raw transcript.

Automated tests cover partials without EOS, disabling/customization, old segment
suffixes, duplicate delivery and the real Unix socket/client callback path.
Human ASR accuracy and noisy-room behavior require acoustic testing.

## Recording-PCM keyword fix (2026-10-21)

Continuous wake ASR may retain a segment whose BOS precedes the recording cue.
The old prefix/suffix guard then discards a genuine `thank you`, even when the
final question transcription contains it. Relaxing that guard admits old speech.

The `aisp-eos` endpoint now warms the existing local Sherpa keyword detector at
CLI wake startup and feeds it only the recorder's post-cue PCM. Each recording
gets a fresh decoder stream and completion scope. Native EOS stays on the existing
sidecar, with its old-segment guards unchanged. Whichever detector completes first
stops recording once; cancellation and restart invalidate old callbacks and queues.
No extra microphone, model download, audio transfer or mandatory silence is added.
The existing ADC sample trim excludes queued cue/pre-recording audio and retains
the first post-cue samples. Keyword workers are armed before PCM collection starts.

The wake YAML remains the only authority for `end_phrase.enabled` and `.phrase`.
The sidecar publishes a derived `/ipc/wake/endpoint-config.json` with its instance
identity and `recording_end_phrase_version=2`; a prepared CLI negotiates
`end_phrase_source=recorder` against that same identity/config. Its shared ASR text
cannot also fire a phrase event. Existing clients without this capability retain
legacy ASR phrase behavior. Disabling the YAML switch disables PCM keywords while
native EOS remains available. Configuration changes require restarting wake;
restart CLI as well for reliable startup prewarming and release verification.

Use the existing installed English Sherpa model configured by
`wake_word.sherpa.model_dir`; if unset, only the existing standard local cache is
checked. Missing assets or reset errors are logged and fall back to the legacy
native endpoint. A native socket failure leaves the current PCM keyword worker
active. This does not guarantee keyword recognition of every accent/noise condition.

Diagnostics: `recording_keyword_prepared source=recorder_pcm` proves preparation;
`recording_endpoint delivered reason=end_phrase source=recorder_pcm` identifies a
keyword stop, and `reason=aisp_eos source=native_asr` identifies EOS. The sidecar's
`end_phrase_partial` logs only segment/recording metadata, so old shared-ASR partials
can be correlated without adding user speech to status logs. Question ASR remains
separate and its final transcript alone is not evidence that live EOS fired.

Regression covers the measured 24.62/240.071/524.261 ms early BOS boundaries,
real socket ownership/config negotiation, recorder sample trimming, competing
completion, cancellation/restart, disabled/custom configuration and failures.
Docker functional probes also use local TTS-generated positive, noisy positive,
acknowledgement/ordinary-speech negative and custom-phrase PCM with the actual
bundled keyword model. No user recording is used as a fixture.
