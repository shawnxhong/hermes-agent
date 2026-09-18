# Local large-ASR wake experiment

Opt-in `wake_word.provider: asr` replaces Sherpa keyword detection, not the main
conversation loop. Requires the resident `openvino_igpu` transcription plugin
(English Whisper large-v3-turbo in this deployment). It never falls back to cloud
ASR or loads a second model. The original Sherpa provider remains available.

`wake_word.asr` settings:

- `aliases`: additional complete phrases; the main `wake_word.phrase` always applies.
- `rms_threshold: 180`: PCM16 segment-start energy threshold, not confidence.
- `silence_seconds: 0.4`: endpoint pause.
- `max_seconds: 3.2`: bounded segment length.
- `min_speech_seconds: 0.16`: minimum above-threshold audio.

Case and punctuation are ignored; full word boundaries are required. There is no
fuzzy semantic matching or prompt steering the transcript toward the wake phrase.
The service's existing Silero gate further rejects non-speech. Recognition is
asynchronous; one request at a time, no unbounded backlog. Pause/resume reset
invalidates old recognition results. Existing playback/recording pause logic is
unchanged. Temporary WAVs are removed after inference, and no ambient transcripts
are logged. Logs contain inference duration and hit/success flags only.

Tradeoffs: repeated surrounding speech consumes GPU, may contend with IM model
work, and can mention the phrase accidentally. Speech while a request is busy can
be dropped; continuous speech crossing the fixed segment boundary may be missed.
Socket timeouts do not cancel already running GPU computation, but stale results
cannot wake after resume. This is a laptop experiment, not a proven recall gain.
Say Hello Intel, pause for the acknowledgement, then ask the question.

Acceptance should compare the same speakers/distances/noise against Sherpa, count
misses and false wakes, and measure speech-end-to-ack, not only ASR inference.
Include unrelated speech, silence, playback, manual record and scene switching.
