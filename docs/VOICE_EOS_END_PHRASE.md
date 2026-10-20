# Native EOS plus an explicit recording end phrase

The wake ASR YAML owns the recording end phrase:

```yaml
end_phrase:
  enabled: true
  phrase: thank you
```

Both native AISP EOS and this explicit phrase can end the same recording.
The first successful notification wins; repeated partials or a later EOS do
not stop it twice. The phrase is recognized from live ASR partial text without
waiting for the SDK EOS flag, so it remains available during continuous noise.
Matching ignores case, normalizes whitespace/punctuation, requires a complete
word-boundary phrase anywhere in the currently recognized text, and uses the
configured phrase rather than a second CLI setting. This remains effective
when other speech follows the phrase without a VAD pause. Mentioning the
complete phrase during recording is itself a control signal.

New YAML sections default to enabled thank you. Existing YAML without the
section receives the same defaults. Set enabled to false to retain native EOS
alone; the phrase remains editable. No ASR hint, VAD threshold or AEC change is
made. Restart the wake service and reconnect the CLI after editing, while idle.

End phrases are recording-scoped; they do not wake the agent or stop playback.
Pre-cue phrases are never replayed. In a continuous old SDK segment, only text
newly appended to the prefix observed at recording arm may supply a phrase;
unknown/revised old prefixes and phrases spanning that boundary are ignored.
Native EOS keeps its stricter post-cue sentence policy.

The ready message advertises the active sidecar setting. The client accepts
end_recording reason aisp_eos or end_phrase for its current recording ID and
preserves the actual reason. The native endpoint name remains aisp-eos for
compatibility. CLI recording hints use the advertised phrase. Transcription
cleanup is unchanged; the control phrase can remain in the raw transcript.

Automated tests cover partials without EOS, disabling/customization, old segment
suffixes, duplicate delivery and the real Unix socket/client callback path.
Human ASR accuracy and noisy-room behavior require acoustic testing.
