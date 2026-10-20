# Accepted wake defaults

The packaged demo-runtime/wake/config.yaml matches the user-tested configuration:
OS AEC with the default playback reference; fan control disabled; recording
volume capped at 20%; native EOS and the enabled "thank you" phrase both end
recording. English ASR uses the Intel hint and ten configured wake aliases.
VAD threshold is 0.5 and block RMS threshold is 0.35.

The final one-second TTS wake buffer waits for playback completion. The normal
greeting-to-beep fixed pause is 50 ms, the beep retains normal playback volume,
and capture is prepared before the greeting. Speak after the beep. See
VOICE_WAKE_BUFFER.md, VOICE_CUE_TIMING.md and VOICE_EOS_END_PHRASE.md for details.

Fan target 30% and release temperature 80 C remain available when a host opts
in. Neither 0% nor 30% produced the expected fan behavior on the validation
machine; do not infer effectiveness from a successful request or cooling state.
Verify real RPM before enabling control on another machine. The 120-second
recording-quiet cap and lease recovery remain enabled.

Persist these defaults in the mounted wake config when deploying. An updated
image does not replace an existing host config. Restart wake and reconnect the
voice session after editing; check audio_capture_ready for effective_aec=os,
since an unavailable AEC reference can cause a fallback to plain capture.
This default update does not migrate credentials, history, queues or models.
