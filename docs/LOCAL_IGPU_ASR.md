# Local iGPU ASR trial

The optional `local-igpu-asr` native plugin registers `openvino_igpu` with
Hermes's existing transcription-provider API. No model prompt, tool schema,
agent harness, wake detector or end-phrase detector changes are required.
Empty speech is represented as a successful empty transcript, not invented
words or a command-provider error. A resident worker owns the GPU model.

Run `scripts/local-ovms/plugins/local-igpu-asr/worker.py --model LOCAL_MODEL
--socket PRIVATE_SOCKET` in an isolated environment containing pinned
OpenVINO GenAI and faster-whisper (for audio decoding and Silero VAD only).
The model directory must already exist; there are no runtime downloads.
The socket's parent must be owned by the user and mode 0700. The socket is
mode 0600; no TCP listener or remote service is used. Zero-length frames are
health probes. Audio and transcripts are not retained or logged by this worker.

Select in config.yaml:

```yaml
plugins:
  enabled: [local-igpu-asr]  # Append to existing enabled plugins; do not replace them.
stt:
  provider: openvino_igpu
  language: en
  openvino_igpu:
    socket: /run/user/UID/hermes-asr/asr.sock
    timeout: 30
```

Model is Whisper large-v3-turbo INT8 on GPU, English-only, greedy/beam=1.
Beam >1 is not used due the GPU RemoteTensor failure observed in evaluation.
Silero VAD (500 ms silence) gates empty/noise-only audio and bounds speech
chunks at 25 seconds. Each chunk is independent. The existing voice-mode
hallucination/stop/end-phrase filters still run after the provider response.
OpenVINO does not expose the same per-segment no_speech_prob/avg_logprob
interface as faster-whisper: do not claim identical confidence filtering or
accuracy. Real accents, names, numbers, light noise and long utterances need
human acceptance. Input limit: 24 MiB / 180 seconds. Failed/dead service
returns an explicit error, never silently switches to cloud or CPU.

Worker startup prewarms VAD and GPU compilation before opening the socket.
Requests are serialized to avoid concurrent model state mutation. Scene
cancellation does not kill shared ASR; abandoned results are not replayed.
The backend selection also affects audio attachments on this Hermes profile;
text IM and ordinary LLM processing remain unchanged.

Deployment units, pinned environment, driver provenance and per-host socket
paths belong in the private ops repository. This is a user-authorized trial,
not a claim that the earlier accuracy/coexistence gates have been passed.
Rollback: set only `stt.provider` back to `local`, preserving `stt.local`, then
restart the CLI if needed; stop the dedicated worker to release its GPU RAM.
