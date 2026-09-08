# General voice task delivery — approved implementation plan

Status: approved by the user on 2026-09-08; implementation in progress.
This document is the source of truth for subsequent development sessions.

## Current implementation checkpoint

Update: full-tool workshop, product-launch, travel and simulated mail-failure
replays now pass. Commit `061de2a84` is pushed and the reviewed legacy-compatible
patch is deployed; `voice_delivery.enabled` is true. Installed-code seven-turn
acceptance passed. See [stability and latency evidence](GENERAL_VOICE_STABILITY.md)
for timings, backup, limitations and required new-session human voice testing.

### Historical Phase 2 release hold (resolved by the update above)

Phase 2 is implemented, but Phase 3 full-tool acceptance has NOT passed reliably.
Nothing from Phase 1/2 is deployed or enabled in the live installation at this
checkpoint. Phase 1 was committed as `94b0eee1e`.

- `agent/turn_workflow.py` supplies a generic buffered continuation on the same
  native AIAgent. No replacement agent, tool-schema changes, approval bypass,
  system-prompt rebuild or prior-message mutation. Only current-turn wire context
  and output budget change. Legacy current-turn display prefixes are removed from
  the wire copy so they cannot truncate the report or ask the model to self-email.
- `general-voice` owns task classification, one ordinary requirements question,
  immutable detailed artifacts, brief summary validation and host-owned delivery.
  Simple answers and explanations do not automatically email. Typed mailbox
  replies use the exact saved body without inference, search or memory writes.
- Travel planning retains the tested travel strategy. Native follow-up adopts its
  saved body and original SMTP receipt, preserving resend deduplication.
- Incomplete generations, silent control markers, cancellation and delivery
  failures cannot be reported as successful email. Summary failure retains the
  artifact and falls back to a short neutral notification, without model retries.
- Turn execution is bounded to six native inference calls and six tool attempts;
  research has at most four searches. Duplicate calls and failed tool families
  stop retrying within the turn. Explicit native actions retain their original
  tool and permission path. These counts are not a hard wall-clock timeout and
  cannot recall a side effect already in flight.
- 238 focused tests pass; 3 other-OS tests skip. Coverage includes real native
  tool execution, SessionDB persistence, interruption, callback cleanup,
  output buffering, ordinary-turn durability and the existing voice bridges.
- Buffered turns use non-streaming native requests. Incomplete output stops
  before the older core's automatic continuation/partial-draft persistence path.
  A verbose explanation is summarized without replacing or emailing the report.
- Real local-Qwen, native-harness capture replays:
  - General workshop, explanation, typed resend and new task:
    `/tmp/hermes-general-voice-x1vsmgen/receipt.json`.
  - Simulated SMTP failure with retained detail and truthful speech:
    `/tmp/hermes-general-voice-43v2f7mn/receipt.json`.
  - Travel strategy plus native explanation and exact typed resend:
    `/tmp/hermes-general-voice-kyhfcu5w/receipt.json`.
- Acceptance exposed and fixed stale-task classification, loss of pending-answer
  identity, long-draft eager persistence, validation of a clipped speech preview,
  false model-generated email status, and silent markers mistaken for artifacts.
  A separate product-launch scenario exposed unnecessary template searches;
  the tightened writing policy reduced that report from three searches to zero.
  This is not sufficient evidence for default-enabled full-tool operation.

### Release hold and exact next step

The legacy-runtime staging overlay is `/tmp/hermes-general-runtime-a0xfy54r`.
Its `deployment-manifest.json` records hashes of the untouched live files. The
overlay is a disposable test artifact, not a deployed installation; the latest
summary-validation change must be refreshed there before reuse.

Full native tool-schema runs use `check_general_voice.py --native-tools` with a
test-only side-effect blocker, real local Qwen, native AIAgent and temporary
SessionDB. Do not confuse this with proof of arbitrary external actions.

- `/tmp/hermes-general-voice-mbrkgtpc/receipt.json`: all task/delivery identity
  checks passed but the model attempted an unnecessary file write; acceptance
  failed the self-contained-drafting/no-tools requirement.
- `/tmp/hermes-general-voice-_2i6a387/receipt.json`: unnecessary malformed memory
  attempts and an incomplete-information response were mistakenly treated as a
  report. Host lexical rejection and a structured `is_deliverable` check were
  subsequently added. This failed receipt is not release evidence.
- `/tmp/hermes-general-voice-asknn5z9/receipt.json`: task/delivery checks passed,
  but another unnecessary file write was attempted and blocked by the test.
  Report completion took about 73 seconds. No user file or real email was written.

Next: stabilize self-contained drafting under the original tool schemas, inspect
actual request context and tool decisions (not only routing), and repeat the
workshop/product-launch/travel acceptance with the final validation changes.
Retain the native harness and permission path; do not silently mask its tools to
make the replay pass. Verify report content, not merely short replies and mail
counts. Only after those checks pass, commit/push any fixes, back up and deploy
reviewed live hunks, run installed-code acceptance, then request human ASR/TTS
testing. The existing travel/wake deployment and live config remain unchanged.

Enable only after staged and installed-code acceptance:

```yaml
plugins:
  enabled:
    - general-voice
    - travel-voice
voice_delivery:
  enabled: true
  default_recipient: xiaoheng.hong@intel.com
```

Keep existing `travel_voice`, model, wake/ASR/TTS, permission, toolsets and IM
configuration. To disable the general behavior, set `voice_delivery.enabled` to
false and restart the CLI; the continuation is dormant and travel remains active.
The shared `voice_delivery.py` module must accompany the refactored travel sender.

The installed core predates this checkout: deploy only reviewed hunks. Its
`build_api_kwargs` lacks the newer provider wrapper; add an equivalent narrow
current-turn wrapper rather than replacing the provider implementation. Its
conversation finalizer returns directly; preserve that shape. Back up every
changed file, including configuration and plugins, before applying the patch.

These are capture-only tests, not new SMTP/inbox or acoustic ASR/TTS claims.
Semantic intent classification and generated report facts remain model-dependent;
normal English/Chinese human testing is still required. Do not claim a universal
intent classifier, offline policy, hard latency SLA, or acoustic acceptance.

## Product contract

The screenless Intel/Lenovo demo uses local Qwen3.6 MoE, local ASR and TTS.
US destinations and English remain the default demo audience. No cloud LLM
dependency is introduced. Wake acknowledgement and turn-start acknowledgement
are separate from task answers and retain their existing behavior.

Do not force two turns for every task:

| Situation | Execution | Delivery |
| --- | --- | --- |
| Simple question | Answer directly | Short speech; no automatic email |
| Complex task, essential information missing | Ask at most one ordinary final question | Brief orientation plus question; ASR follow-up |
| Complex task, enough information | Execute immediately | Short summary plus host-owned email of complete result |
| Follow-up on an existing result | Native Hermes harness with the saved result available | Still short speech; updated detail emailed only when warranted |
| A genuinely new task in the same session | Create a new task identity | Re-evaluate simple/complex and missing information |
| Explicit resend/address change | Reuse the exact selected artifact | No inference/search/memory update; truthful delivery status |
| Coding, typed CLI, IM | Existing harness | Existing surface policy; no automatic general voice email |

A typed mailbox is accepted only as a direct answer to an outstanding delivery
question in that same local voice task. Do not infer voice modality from text.
An email-shaped string elsewhere is not authorization to send an old artifact.

## Architecture and invariants

Separate three concerns:

1. **Task router/state:** identify new task, continuation, simple answer, coding
   or cancellation. Task identity is independent of the session and turn number.
   Extract only facts supported by the user's current input or active task.
2. **Execution:** scenario skills guide domain work; native harness handles
   general tasks and follow-ups. Retain real tools, cancellation, approval and
   ASR clarification bridges. Do not simulate completion with a planner response.
3. **Delivery:** store detailed artifacts separately; validate a short spoken
   summary; programmatically submit mail and append truthful status afterwards.

The third turn does NOT disable speech constraints. Hard-cap spoken summaries
before the final TTS cap; do not trim the full report and call it a summary.
Keep raw structured output and long drafts out of streaming TTS.

Do not mutate the main system prompt, tool schema or past conversation to change
tasks. Inject selected artifact/context only through a reviewed current-turn
execution seam. Preserve native finalization, role alternation and SessionDB.
Keep approvals in their native path; mail-delivery authority is not authority
to book, purchase, send unrelated messages or modify settings.

## State and artifacts

Profile-aware SQLite state under `$HERMES_HOME/cache/voice-delivery/`:

- Session points to one active task; each new task gets a UUID.
- Task records request, facts, phase, whether its one question was used, language,
  and an optional pending mailbox question. Never store temporary mailboxes as
  global preferences or memory.
- Immutable artifact revisions retain detailed content and its spoken summary.
  Follow-ups reference the correct task/artifact rather than reconstructing it
  from summary-only conversation history.
- New tasks do not inherit old facts, body or recipient overrides. Explicit
  updates/resends use the selected artifact; unrelated tasks cannot silently
  resend it. Store revisions even if SMTP is unavailable.
- Reserve each submission durably before SMTP; accepted, failed/uncertain and
  in-flight outcomes are distinct. Never auto-retry ambiguous SMTP outcomes.
  Task/revision checks reject stale workers before external I/O.

Default recipient remains `xiaoheng.hong@intel.com`. Per-delivery overrides must
come from the user's input. Missing address uses an ordinary spoken question.

## Bounded routing and execution

- Use one small, local-only structured routing request; at most one repair.
  Validate output on the host. Lexical fast paths may cover deterministic
  controls (a pending mailbox answer, cancellation), not universal intent.
- Only ask for information that materially changes the result. If already
  asked once, use safe explicit assumptions; do not invent critical facts.
  A task requiring essential authorization/unsafe assumptions must remain
  blocked in native clarification/approval rather than pretending completion.
- Tool/time budgets are task-specific. Travel retains its proven two searches;
  other tasks must not inherit that number indiscriminately.
- Repeated failures must terminate with partial-result/uncertainty information.
  Do not claim a wall-clock deadline can recall an already-started side effect.
- Generation, interruption and delivery failures cannot fall back to uncontrolled
  long speech or a false successful-send statement.

## Implementation sequence

1. **Foundation:** reusable task/artifact store, shared at-most-once mail
   submission primitive, task-router schema and local routing tests. Integrate
   shared mail mechanics without changing the tested travel behavior.
2. **Harness integration:** current-turn execution context, buffered output and
   structured delivery; verify native tools, approvals, cancellation, streaming
   and persistence. No blanket tool disabling or isolated mock-harness substitute.
3. **General scenario acceptance:** simple questions; drafting/research/planning;
   task switch while awaiting details; updated report; explanation-only follow-up;
   coding and IM isolation; typed recipient; send/search failure; duplicate and
   cancelled turns; English/Chinese; local-only inference.
4. **Deployment:** commit/push, per-file backup, narrow live patch, default-enable
   only validated integration, installed-code replay, then human ASR/TTS test.

The foundation alone does not enable the general workflow. Keep the currently
accepted travel/wake behavior active until the harness integration is validated.

## Acceptance evidence to record

For each live local-model replay record route, task ID, artifact revision,
question count, inference/tool counts, final speech length, SMTP capture/status
and task-switch behavior. Use capture-only email by default. Automated tests use
temporary Hermes homes and the repository test wrapper; exercise real SQLite,
native plugin discovery and AIAgent/SessionDB, not only prompt snapshots.

Known risks: semantic misrouting by the local MoE, summary factual drift,
incomplete artifacts on token exhaustion, and accidental side-effect bypass.
These are release gates, not reasons to weaken native permissions or deploy an
unverified replacement for the working demo.
