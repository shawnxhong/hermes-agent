# Local OVMS initialization warmup

Implementation starts from app release `docker-20261013.1` (`fb3411327`).
The demo CLI variants and the common CLI call `warmup_for_cli` before starting
the input-processing and wake threads. The existing voice-ready cue follows it.

The warmup initializes the native agent, restores/builds its session prompt via
the existing persistence path, and makes one discarded inference request. It
uses the real system prompt, ephemeral prompt, prefills, resumed history and
native request builder, including tool schemas and template/reasoning settings.
Only the output budget (one token), streaming and request timeout differ. It
does not execute generated tools, invoke the conversation loop, append history,
call response callbacks or synthesize speech. The first normal turn reuses the
prepared prompt. Existing scene/reset invalidation remains unchanged.

Automatic warmup is limited to interactive voice, the `custom` provider, the
`chat_completions` transport and HTTP loopback `/v3` endpoints. Text sessions,
single-query sessions and cloud routes skip it. Provider resolution is checked
again before sending. This targets the accepted OVMS demo without changing
ordinary Hermes/cloud behavior.

## Configuration and readiness

```yaml
voice:
  llm_warmup:
    enabled: true
    timeout_seconds: 90
```

Defaults apply to existing configurations; no production YAML migration is
required. Disable with `enabled: false`. Timeout must be positive and at most
300 seconds. Before inference, poll the model-specific KServe endpoint
`/v2/models/<encoded model name>/ready`. OVMS starts its HTTP listener before
registering/loading the model: connection failures, 404 and 503 are retried
within the same budget. Other HTTP errors fail open immediately. Show a waiting
message during loading and confirm readiness only after warmup inference succeeds.
Preparation and readiness time are subtracted from the inference timeout. Agent
construction itself uses the existing initialization timeouts: this setting is
not a hard deadline on every import/provider/tool discovery step. The SDK
request has retries disabled and owns a separate HTTP client, so cleanup does
not close the foreground agent's client. Ctrl+C cancels startup normally.

Initialization failures and request timeouts log the exception type and continue
ordinary startup. They do not claim successful warmup. Logs include
`local_llm_warmup status=... elapsed_seconds=...`, without prompt text or provider
error bodies. Startup time increases because inference preparation moves before
the first question; a measured first-answer improvement is required for release.

Warm state belongs to the local server. Restarting OVMS or evicting prefix blocks
can lose it. A CLI scene reset builds its native prompt again; this implementation
does not prefill every scene at startup or keep issuing background requests.

## Disk cache feasibility, investigated 2026-10-13

| Cache | Existing support and decision |
| --- | --- |
| System prompt text | Hermes already writes the session prompt to SQLite and restores it for resumed conversations. Reuse that path. Text on disk does not avoid LLM prefill. |
| In-memory prefix state | OpenVINO GenAI exposes `enable_prefix_caching`; it can retain completed prefix blocks while the server remains resident. Confirm its effective setting and cache-hit behavior in the exact pinned OVMS build. |
| Compiled model | OpenVINO/OVMS model compilation caches can reduce server load/compile time; they are separate from processed system-prompt state. Trial a host-local writable cache mount only after confirming this build propagates cache settings to `VLM_CB`. |
| Disk prompt/KV state | No supported save/load interface was found to rely on. Upstream's disk-persistence prototype was closed without merging, and the follow-up feature request remains open. Do not add undocumented dump/load flags or replace the pinned backend. |

Sources:

- [GenAI SchedulerConfig](https://docs.openvino.ai/2025/api/genai_api/_autosummary/openvino_genai.SchedulerConfig.html)
  describes in-memory block retention and release when prefix caching is off.
- [Persistent KV-cache request #4091](https://github.com/openvinotoolkit/openvino.genai/issues/4091)
  reports the missing disk persistence capability, verified by its author against
  2026.2.1. This is upstream evidence, not a test of this installation.
- [Closed disk-cache prototype #3209](https://github.com/openvinotoolkit/openvino.genai/pull/3209).
- [OVMS compilation-cache propagation report #4230](https://github.com/openvinotoolkit/model_server/issues/4230)
  shows why a cache directory alone is insufficient proof for continuous batching.

The accepted launch recipe uses `VLM_CB` and does not explicitly pass a prefix
cache flag or bind a disk cache. Subsequent Docker inspection verified OVMS
`2026.2.1.1122f03bf`, OpenVINO `2026.2.1-21919-ede283a88e3`, and GenAI
`2026.2.1.0-3123-7dea0459b2a`. The installed CLI reports
`--enable_prefix_caching` defaults to true, and the live launch has no override.
Local-model observations support prefix reuse, but no cache-hit counters or
compiled-model disk cache have been measured. Disk KV persistence is still not
provided by the inspected CLI.

Recommendation: keep OVMS resident, warm the real prefix at CLI initialization,
verify in-memory reuse, and investigate compiled-model persistence separately.
Persistent prompt-state storage needs a supported backend API or a separately
scoped backend change. Any future serialized state needs exact model, tokenizer,
template, prefix and runtime/precision compatibility checks and safe fallback.
Caches stay host-local; they never enter Git or the portable asset baseline.

## Validation and remaining acceptance

Canonical tests:

```sh
scripts/run_tests.sh tests/cli/test_voice_llm_warmup.py tests/cli/test_voice_ready_cue.py tests/cli/test_voice_latency_warmup.py
```

Standalone offline checks (stdlib only):

```sh
python scripts/local-ovms/check_init_warmup.py
```

The isolated candidate smoke command adds `--integration`. It creates temporary
`HERMES_HOME` state and an HTTP fixture, constructs the real `HermesCLI`, runs
warmup and a native conversation, and checks identical system/tool prefixes,
clean history and a still-open foreground client. It uses no GPU or audio.
The candidate includes the exact test file used by the checker. The final rc4
image passed this integration check and 75 focused canonical tests for warmup,
readiness, ASR prewarm, scenes/reset and prompt restoration. Scene test fixtures
mock the host media bridge and use selected skill identifiers, preserving their
behavioral assertions across the accepted skill renames. The runner needs a
writable `test_durations.json` bind in an otherwise read-only test container.

The exact rc4 image also passed a real-model warm-first probe: warmup returned
ready in 2.941 s and the first visible response delta arrived in 2.061 s. OVMS
was already resident and its prefix was previously exercised; these are single
observations, not boot-cold or acoustic timings. The probe uses the accepted
`/home/hermes` working directory, 13,137 system characters and 32 native tools,
discards a bounded response, and never executes tools or audio. Its generated
state is disposable; production histories and queues are not mounted.

The earlier one-token diagnostic observed 13.251 s on its first request and
1.296 s after warming the same prefix. That used rc3's identical warmup code;
one-token probes can produce no visible SSE text when the token is a hidden
control token. The final real-model check permits a short 32-token reply for
visibility while warmup itself remains capped at one token.

Then compare fixed-question trials with warmup enabled/disabled: CLI launch to
ready, question submission to first token, and first audible response. Separate
fresh CLI with resident OVMS from fresh OVMS, and report cold observations
separately from warmed medians/p95. Verify effective prefix caching, memory use,
timeout fallback, Ctrl+C, repeated wakes, all four scenes and reset. Use isolated
candidate state and a single audio/GPU stack during a maintenance window. Do not
claim end-to-end speed from a tiny generic model request or an HTTP fixture.
