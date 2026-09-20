# Independent AISP partial-result wake experiment

Parent: immutable docker-20260920-media-stop.2. Target: box_a only.
Keep all existing aliases and all host audio device settings. English-only
live-asr; no SDK agent, LLM, TTS or speaker diarization. Existing Hermes capture
feeds PCM to an independent Docker service via a private Unix socket. AISP
models stay loaded across turns; only waiting-for-wake sends audio.

Every partial and final is logged in bounded local rotating JSONL, including
timestamp, instance/listen epoch, original text and decision. Match immediately
on any partial; no EOS/stability requirement. Same timestamp after a trigger is
logged but never retriggers. A stopped listening epoch cannot dispatch events
into a new epoch. Disconnect disables matching; wait/flush drains old callbacks
and resets VAD before the next client is acknowledged. The profile component of
a wake match is empty: this provider is not a profile named 'asr'.

Interaction remains wake -> existing acknowledgement/beep -> separate question
-> 'thank you' -> existing Hermes ASR/agent/TTS. No command tail forwarding.
No prompt, skill, mail, media-stop or gateway changes. Service failure is visible,
not a silent Sherpa fallback; Ctrl+B remains available.

SDK 0.3.6.0 supplied in Music needs glibc 2.43 and includes no Linux Python
binding. A small C ABI shim calls the public C++ live-asr API; the independent
service uses Python stdlib. Hermes stays on its Ubuntu 24.04 base; the service
uses pinned Ubuntu 26.04. SDK archives/models/logs must not enter Git.

Production acceptance requires human multi-speaker recall and false-wake tests.
Keep the parent image/config snapshots and stop the sidecar on rollback.

English-only compatibility: supplied Whisper code ignores its m_language field,
and the bundled GenAI JSON constructor ignores language/task (verified by a
compiled config probe returning UNSET). Therefore the service creates a temporary
symlink view of the read-only model and limits generation_config.lang_to_id to
the existing English token. GenAI's language detector chooses exclusively from
that map; the C++ bridge asserts the loaded map has exactly that one token.
Original model/config is untouched. This constrains decoding, not output filtering.
Reference: https://github.com/openvinotoolkit/openvino.genai/blob/master/src/cpp/src/whisper/models/decoder.cpp
