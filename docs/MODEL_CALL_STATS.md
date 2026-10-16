# Lightweight model call statistics

Each native agent-loop provider execution attempt writes one `model_call_stats`
JSON record to the existing agent log. It applies to CLI/voice and Gateway;
provider calls made outside that loop (for example auxiliary title generation)
are not measured by this collector. No request/response content, credentials,
URLs or tool arguments are logged. No sampling thread or extra model request.

Fields:

| Field | Meaning |
| --- | --- |
| request_id / attempt | Native API request ID and outer execution attempt number |
| session_id / platform / model / provider | Correlation metadata |
| duration_ms | Monotonic elapsed time around execution, including client-side waits/retries inside that attempt; excludes later tool execution |
| ttft_ms | Client-observed time to first nonempty streamed text, reasoning or tool name/arguments; empty role/finish/usage chunks do not count |
| input_tokens / output_tokens / total_tokens | Provider-reported usage; no token estimates |
| cached_input_tokens | Explicit usage.prompt_tokens_details.cached_tokens, including a reported zero |
| queue_ms | Explicit x-queue-time-ms response header or usage.queue_time (seconds converted to ms); never estimated from TTFT |
| cache_source / queue_source / ttft_source | Measurement provenance or unavailable |
| provider_request_id | x-request-id if returned; native request_id is always available |
| status / error_type | response returned, or error class; response does not assert tool/task success |

Unavailable data is JSON null, not zero. Nonstreaming responses have null TTFT.
Current local OVMS response was checked: usage contains prompt_tokens,
completion_tokens,total_tokens, but no queue duration or cached-token details.
Therefore queue_ms and cached_input_tokens remain null on this backend until
it exposes supported measurements. Aggregate queue histograms/cache utilization
cannot be substituted for a per-request measurement. OVMS is not rebuilt or
reconfigured by this instrumentation.

The collector measures actual streamed provider output before any stop-policy
fallback creates a tool call. Late output cannot modify a finished record.
SDK/internal stream retries are included in execution duration but not counted
as separate outer attempts. Unsupported provider streaming formats leave TTFT
unavailable rather than reporting response completion time as TTFT.

To view records:

```sh
tail -F /home/intel/hermes-runtime/docker-20261015.3/state/logs/agent.log /home/intel/hermes-runtime/docker-20261015.3/gateway-state/logs/agent.log | grep --line-buffered 'model_call_stats '
```

Existing log handlers/rotation are retained. No new per-token log output.

Laptop integration additionally isolates collector initialization and finalization
failures at the execution boundary. A telemetry failure must not create retries,
change answers or skip request-active cleanup. Current laptop logs are under
`/home/agentdemo/hermes-releases/runtime/laptop-docker-20261015.2/{state,gateway-state}/logs/agent.log`;
the directory name remains unchanged when a candidate image is selected.
