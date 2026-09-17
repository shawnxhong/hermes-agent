# Stage 1: opt-in local tool protocol diagnostics

This is observation, not a stability repair. No retry limits, UI suppression,
prompt/tool policy, scene keyword routing or runtime deployment is included.
Production remains 264348292; candidate code starts from dd99230d9.

## Interface and privacy

In an isolated diagnostic process, attach a `ProtocolCapture` from
`agent.tool_protocol_diag` to `agent._tool_protocol_capture`. It is absent by
default and gated to loopback providers. There is no persistent config change.
Pass `wire=True` explicitly to include redacted SSE envelope events. This records
wire index/id relationships, known tool names, lengths and terminal markers,
NOT raw request/response bodies, text, parameter values, headers or credentials.
Identifiers use a capture-local salted digest, stable within that capture only.

The exclusive-created JSONL file is mode 0600, at most 4,000 events / 2 MiB;
extra events are dropped, counted by `capture.dropped`, not rotated indefinitely.
The standalone tool creates a mode-0700 temporary profile. Trace failures are
best-effort and cannot change normal inference/dispatch. Wire bytes and chunk
boundaries are forwarded unchanged. Oversized SSE lines (>64 KiB) are counted
and omitted from wire analysis without suppressing their delivery to the SDK.

```bash
venv/bin/python scripts/local-ovms/diagnose_tool_protocol.py \
  --profile /path/to/hermes-profile --wire
```

The script copies candidate repository plugin/skill assets and selected settings
into its temporary profile and uses the profile's enabled plugin combination,
CLI toolsets, system builder,
and real voice prefix. It sends ONE model request with a diagnostic-only 512-token
cap, never dispatches returned tools, and never enters the full routing/answer
workflow. No email/audio/state-changing tool action is performed. It is useful
for protocol evidence, NOT full production-path acceptance or latency claims.

## Event interpretation

- `request_start`: frozen turn/logical API request identity, previous request link.
  Native outer generations may have new request IDs. This stage links them but
  does NOT claim to group all semantic recovery into one budget; stage 4 owns that.
- `attempt_start/error/returned/cancelled`, `retry_scheduled`: distinguish retries
  from many tool slots in one attempt. Attempt IDs stay distinct across reentry.
  `returned` does not mean successful completion: inspect finish and assembly.
- `first_token`, `tool_delta`: wire vs SDK, index/id, argument bytes and repeated
  adjacent-fragment flag. Timestamps identify first tool name and last observed
  new fragment. A repeat is evidence, not proof of an invalid generation.
- `assembled_slot`: raw index -> accumulator slot (same index/new ID is a special
  compatibility path). `assembled_arguments` checks JSON object validity BEFORE
  existing repair; `assembly_end` records missing finish explicitly.
- `argument_validation`, `execution_start`, `execution_terminal`: parsed arguments
  vs actual authorized dispatch. Blocked/invalid calls can terminate without a
  start. Execution IDs correlate within a turn; tool results are not captured.
- `stale_chunk_discarded`: existing stale-writer fence acted. Observation does not
  introduce new cancellation or authorization guarantees.

## Evidence and limits

Deterministic tests feed real OpenAI SDK SSE decoding into the actual Hermes
stream accumulator, not a replacement parser. Cases include valid arguments,
missing finish with the reported malformed bridge shape, index reuse with new
IDs, 40 tool slots in one response, transport drop/retry, privacy/file limits,
disabled-recorder equivalence, and actual tool middleware dispatch/scope denial.
Fixtures are synthetic reproductions of failure CLASSES, not recovered raw
packets from the user's historical session.

One real local OVMS probe used the reported bedroom query, enabled production
plugin combination/toolsets and voice prefix. It returned one `tool_describe`
call with valid JSON, finish `tool_calls`, one attempt. Wire and SDK both recorded
nine tool deltas; one assembled call, zero executed tools, zero dropped records.
Elapsed request observation was 22.55 seconds; this is not ASR/TTS or full-task
latency. Local receipt: `/tmp/hermes-protocol-diag-67c3a7w7/protocol.jsonl`.

After restricting asset copying to candidate repository assets, the final script
was checked again: one valid `skill_view` call, one attempt, nine deltas per layer,
zero dispatched tools/dropped records, 23.81 seconds. Receipt:
`/tmp/hermes-protocol-diag-y1ammggm/protocol.jsonl`. Tool choice varied; neither
single-request probe is evidence of stable scene selection or task completion.

Validation: eight focused files / 97 tests passed with `-k 'not Anthropic'`;
the final added malformed-dispatch case then passed in the 14-test diagnostic
suite (these counts overlap). The initial unfiltered suite had three Anthropic
SDK import failures because that optional package is absent in the local runtime
environment. No production dependencies were installed to hide that limitation.
Adjacent coverage includes stream accumulation, partial finish, retry/interrupt,
single-writer fences and executor context propagation. Production remains
264348292 and its config SHA256 remains
`49cf4149d9071d33f8263dc04963ac1e3ade79f3f8a21a0f2705e99ee08e608a`.

The historical dozens-of-preparing incident did NOT recur in that one probe.
We cannot retrospectively attribute each historical line to OVMS vs client vs
retry without its raw stream. Future captures can now distinguish those layers.
The unchanged native missing-finish recovery can still fail/loop; stage 1 does
not claim the reported user problem is fixed. Review before stage 2.
