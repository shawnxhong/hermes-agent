# Stage 1 diagnostic reliability review — 2026-09-17

Scope: repair draft PR #13 on top of `97a81bcc0`. No retry limits, routing,
prompts, permissions, scene policy, runtime configuration or deployment changes.
Do not proceed to stage 2 or merge until review. The production rollback target
and running release remain `264348292419a4644b1953dd08a9b812caf9d03e`.

## Findings and evidence

Eight minimal regression probes were run before changing the implementation:
**8 failed, 0 passed**. They reproduced all four findings, not the historical
real-user model loop. The same eight pass after repair.

| Finding | Before | Repair and coverage |
| --- | --- | --- |
| Diagnostic failure changes inference | Surrogate parameter raised UnicodeEncodeError | Tolerant byte counting without modifying arguments; whole observation boundaries isolated; recorder method, write and flush failures tested through actual SDK/accumulator and sequential dispatch |
| Late execution attributed to newest request | Both same-turn and cross-turn probes used R2 for R1's tool | Freeze slot origin, bind returned calls, propagate per-batch execution context into existing worker threads; reused or unknown IDs without binding explicitly unknown |
| Multiline SSE reported invalid | LF/CRLF/CR probes failed, despite SDK accepting hello/stop | Installed SDK SSE field decoder, bounded event framing, one JSON parse per event; byte/chunk passthrough, single-byte boundaries, empty chunks between CR/LF, DONE and oversized-event recovery |
| Terminal status null | Successful/error results both recorded null | Existing model_tools result classifier; additionally observe accepted concurrent batch results, whose normal lifecycle hook bypasses the sequential terminal helper |

The full executor tests use a registered harmless synthetic handler through the
real AIAgent, registry and sequential/concurrent dispatch. Cases: success,
returned error, raised exception, policy denial, timeout, pre-dispatch cancel,
and in-flight cancel (14 cases). Timeout/cancel workers are released and joined
before checking logs, so late completion cannot hide behind closing the file.
Every operation has one terminal; executed operations have one corresponding
start with the same execution identity. Blocked/pre-cancelled operations have no
start. A newer turn/request reuses the same tool ID in these tests; frozen origin
still identifies the original request and turn.

The on/off comparison matrix contains 18 pairs: nine diagnostic conditions
(healthy, write failure, flush failure, assembled/chunk/slot/wrap/bind/event
observer exceptions), each with and without an initial transport drop. It uses
real OpenAI SDK decoding and Hermes accumulation/execution; the external tool
handler is replaced by a harmless deterministic result. In every pair:

- Response finish reason and raw arguments (including a lone surrogate) match.
- Returned tool-message semantic fields match (not timestamps).
- No drop: one API attempt, one tool execution in both configurations.
- Initial drop: two API attempts, one tool execution in both configurations.

These comparisons exercise existing recovery; they do not introduce retries or
claim to cap recovery time. Separate real registry tests cover handler dispatch.

## Validation

Final focused suite: **56 passed** (42 review cases plus 14 original cases).
Final combined run: **145 passed, 0 failed** across 10 files (includes those 56).
Adjacent suite is run with `-k 'not Anthropic'`: the optional Anthropic SDK is
absent, as disclosed in the original stage-one record. No dependency was
installed in production. Run with the project's isolated runner:

```bash
scripts/run_tests.sh -j 4 -q \
 tests/agent/test_tool_protocol_review.py \
 tests/agent/test_tool_protocol_diag.py \
 tests/run_agent/test_stream_drop_logging.py \
 tests/run_agent/test_streaming.py \
 tests/run_agent/test_stream_interrupt_retry.py \
 tests/run_agent/test_partial_stream_finish_reason.py \
 tests/run_agent/test_tool_executor_contextvar_propagation.py \
 tests/run_agent/test_stream_single_writer_65991.py \
 tests/agent/test_stream_single_writer_guard.py \
 tests/run_agent/test_sequential_tool_timeout.py -k 'not Anthropic'
```

No new real-model requests, audio or email actions were performed in this
review repair. The two prior OVMS probes remain single-request observations,
not evidence that the original scene-selection/generation-loop issue is fixed.
Production config SHA256 is unchanged:
`49cf4149d9071d33f8263dc04963ac1e3ade79f3f8a21a0f2705e99ee08e608a`.

Review diff: `git diff 97a81bcc0 HEAD` after the reliability commit; only the
diagnostic module, its three integration points, tests and documentation belong
in this change. No stage-two implementation is included.
