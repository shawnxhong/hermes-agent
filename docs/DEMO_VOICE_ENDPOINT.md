# Demo recording endpoint

The Docker release uses `scripts/local-ovms/voice-endpoint-profile.json` as
the versioned `voice.end_phrase` override. Say **thank you** to finish recording.
The existing detector strips the terminal phrase before sending the transcript
to the agent. `hint_file: null` disables both the first-wake spoken instruction
and the manual-recording instruction. The normal wake greeting remains.

This does not change Hello Intel wake detection, the separate stop command,
silence timeout, ASR or TTS models. Speech containing the endpoint phrase can
end recording, so reserve it for the end of a request.

Bake the profile into the accepted runtime layer and migrate only these three
configuration fields using the ops release migration. Do not copy the whole
overlay source tree over the separately pinned core. Human microphone acceptance
is required before promoting the candidate to the fleet baseline.
