# General voice task delivery — approved implementation plan

Status: approved by the user on 2026-09-08; implementation in progress.
This document is the source of truth for subsequent development sessions.

## Current implementation checkpoint

Phase 1 is implemented in the canonical development checkout, not deployed:

- `hermes_cli/voice_delivery.py`: profile-aware task/artifact store, optimistic
  revisions, one requirements-question claim per task, narrowly scoped typed
  recipient answers, and durable at-most-once SMTP submission.
- `hermes_cli/voice_task_router.py`: local-only non-streaming structured router,
  one bounded repair, trusted modality gating, native-action/coding routing.
- Travel now calls the shared submission primitive in development, preserving
  its original database and fingerprint. No old receipt migration or resend.
- 198 focused tests pass; 3 non-Linux tests skip on this host.
- Nine real local-Qwen routing cases pass (English/Chinese, simple/complex,
  requirements answer, result follow-up, new task, coding and external action).
  Receipt: `/tmp/hermes-voice-router-wcrpav1i/receipt.json`.
- The first routing replay mislabeled a requirements answer as a follow-up.
  Host validation now uses the outstanding-question/no-artifact state to
  distinguish execution from follow-up. A regression covers this distinction.
- A real local-Qwen travel replay with the refactored sender passes planning,
  typed recipient resend with identical body, and subsequent native arithmetic.
  Mail was captured only. Receipt:
  `/tmp/hermes-workflow-check-hz80i5en/receipt.json`.

Phase 2 remains required: connect routing and artifacts to native harness
execution and host-owned summary delivery, preserving approval/clarification
callbacks and preventing raw streaming before finalization. A router result is
NOT task completion, and `task_summary` must never replace the actual user input
as execution authority. No general auto-email plugin is registered or enabled
at this checkpoint. The live demo and its configuration are unchanged.

Next work starts at `agent/conversation_loop.py`'s native `run_turn_workflow`
seam and `agent/turn_finalizer.py`. Existing `transform_llm_output` alone is too
late to prevent raw streaming and is timeout-wrapped; do not put SMTP under an
abandon-on-timeout transform callback. Review a per-turn, buffered continuation
contract with native harness execution rather than resetting the parent agent's
prompt/tools or spawning a reduced-capability substitute.

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
