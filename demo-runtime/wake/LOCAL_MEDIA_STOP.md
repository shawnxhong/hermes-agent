# Local media stop

The continuously running wake ASR service recognizes the same exact English
stop-command forms as the Feishu media-stop policy. The single matcher lives in
`tools/media_stop_policy.py`; examples include `stop music`, `stop playback`,
`Please close the video.` and `Could you stop the music please?`. Bare `stop`,
Chinese commands, quoted/negated requests, compound tasks and wake-word-prefixed
commands are outside this grammar. Punctuation/case handling is unchanged from
Feishu; this is whole-string matching, not keyword substring search.

Qwen3 ASR receives four wake hotwords, `Hello Intel`, `Hi Intel`, `Hey Intel`,
and `Intel Intel`, plus media-stop hotwords through the SDK `asr.context`: `stop
playback`, `stop music`, `stop video`, `stop audio`, `stop media`, `stop player`,
`close video`, `close player`, `turn off music`, and `turn off video`. These hints
are appended to caller-supplied `--context`, including when that argument is
empty. The four canonical wake phrases also merge into the configured wake aliases
at service startup and each wake-client subscription. Existing aliases remain;
an older client cannot remove `Intel Intel` by sending its original alias list.
The obsolete `thank you` end phrase is not included. Stop command grammar is
unchanged.
Hints are line-separated. A fixed `Intel Intel` waveform caused an early partial
to copy the whole comma-separated vocabulary; the same waveform passed with
line-separated hints. The regression checks every partial for inserted stop/end
phrases, alongside silence and unrelated-speech probes. This does not establish
that hallucinations are eliminated for other audio or physical microphones.

A matching partial immediately queues the existing device-wide media bridge
`stop` RPC. No wake word, active client subscription or EOS is required. A later
ASR correction cannot undo a stop. All subsequent results for that segment's
timestamp are consumed, including empty EOS, without sending a wake or recording
event to Hermes. A subsequent normal sentence still uses the existing wake/EOS
path. Already-streamed audio in a separate recorder is not erased by this service.

The RPC runs on one background worker, not on the SDK callback or server lock.
The queue holds 32 requests and dedup remembers 1024 segment timestamps. A full
queue, failed response or 10-second transport timeout is logged, never retried
automatically; a new spoken sentence may try again. Stop uses the existing
bridge policy: same-user supported media players and current/queued TTS. It does
not add browser-tab control or change audio routing, SDK/AEC settings or Hermes
conversation state. Speech from playback is not distinguishable from user speech
by this phrase matcher; acoustic false-trigger behavior requires live testing.

The wake image must package the standard-library-only modules
`tools/__init__.py`, `tools/media_stop_policy.py` and `tools/playback_control.py`
under `/opt/qwen-wake/tools`. Bind the existing host media IPC directory to
`/ipc/media` with the same UID as the bridge. Bind the directory rather than the
socket file so bridge restarts can replace the socket. No Docker socket, host
PID namespace, network or additional capabilities are required.

`events.log` records `media_stop_matched`, `media_stop_completed` and
`media_stop_failed`, including the SDK timestamp, request duration and reason.
Only a confirmed inactive/stopped response is logged as completed; logs do not
claim physical silence. No spoken acknowledgement is generated.

Build smoke: `python3 /opt/qwen-wake/check_media_stop.py`. Regression tests:
`tests/tools/test_wake_media_stop.py`, existing Feishu policy, cross-channel
stop, TTS cancellation and AISP EOS suites. The public launcher continues to be
`hermes-box-voice start`; the deployment control must select the candidate images
and media mount. Roll back the Compose/control selection to restore the baseline.

## Unified wake configuration (supersedes hardcoded vocabulary above)

`demo-runtime/wake/config.yaml` is the versioned default. Deploy it as a
read-only directory mount, then launch `service.py --config /config/wake/config.yaml`.
Only this YAML supplies `asr.hotwords` and `wake.aliases`.
The hotword list is joined with newlines into the SDK property `asr.context`;
there is no separate user-configurable context field.
Empty `asr.hotwords: []` disables hints. Missing/invalid configuration fails
startup; no literal vocabulary fallback is appended. Hints remain one per line.
The service-owned aliases remain authoritative across client subscriptions:
legacy clients cannot reintroduce an alias removed from the service config.

The packaged default retains four canonical wake hints, ten stop hints and all
previous recognition aliases. Stop *matching* still uses the shared Feishu
command policy; ASR hints are recognition bias, not a stop-command allowlist.
This is a wake-only configuration, not the private Hermes credentials/config.

Edit the mounted host YAML and restart the `aisp-wake` Compose service. No image
rebuild is required. `hermes-box-voice start` alone does not restart an already
running wake service. The image contains the same default for standalone use.
The wake image gets the pure-Python PyYAML 6.0.3 package and its license from a
pinned, already-local Gateway image; no network package installation is needed.
