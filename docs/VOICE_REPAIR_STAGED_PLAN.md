# Voice repair after dd99230d9: approved staged plan

Approved 2026-09-17. Candidate base:
`dd99230d98d34a564be01e556d8ff93fb6b35ba6`. Laptop MUST remain on
`264348292419a4644b1953dd08a9b812caf9d03e` until explicit acceptance.
Retain both releases/environments and the unchanged config fingerprint.
No automatic deployment, Box rollout, or reuse of the abandoned
home-tool-stability keyword parser. Stop for human review after each stage.

## Accepted product decisions

- One model router understands the request; the program enforces task scope.
  No second classifier or scene keyword intent parser.
- Buttons choose the initial scene; a new spoken task may automatically select
  another configured scene. Follow-ups preserve their task; independent tasks
  revoke previous pending authority and do not inherit old skill instructions.
- General questions and coding remain general. Complex work without a matching
  skill must still work; scene and answer complexity are separate decisions.
- Simple stable questions may receive their direct natural answer in the routing
  call, with no second answer/summary call. That exit cannot invent tool/action
  success or current evidence. Invalid routing uses shared recovery, then native
  answering within the existing scope, not an extra classifier.
- Completed long results are retained in full, summarized once for speech, and
  queued for email. Already-short results are not rewritten. Program-owned mail
  status distinguishes queued/sent/failed. Failed/incomplete work is not mailed
  as a completed deliverable. Summary failure does not trigger repeated rewriting
  or reading the whole report aloud.

## Stages and review gates

1. Diagnostic correlation and deterministic failure replay, no policy changes.
2. Remove global scene behavior prompts; scope skill discovery/loading and direct
   and bridged tool dispatch using one task/session/generation identity. Directly
   expose the active scene's small concrete schemas. Compare with the bridge on
   real OVMS before accepting. Remove home skill's "every turn" instruction;
   read-only ambiguous room status may report all matches; control needs targets.
3. Extend the existing router with configured scene, handling mode and optional
   direct short answer. Keep generic voice rules global, task rules scoped.
   Keep execution prompts/tools stable within a task/retry. Preserve full audit
   history but build new-task model context without old scene instructions.
   Short output bypasses compression; long output gets one direct-user summary
   without new facts, narrated-document style, or model-generated mail claims.
4. Shared recovery accounting, typed error reasons, real cancellation and one
   pending clarification per turn. Stream retries, outer recovery and corrective
   generations consume the same state. Each logical request gets at most one
   recovery, each turn at most two; successful tool work does not consume this
   error budget. Repeated protocol failure stops. Missing finish is not proof of
   excessive payload; do not increase output limits blindly. Never replay a write
   whose execution happened or is uncertain. Timeout is not authorization.
5. Candidate acceptance against both old baselines, then separate approval to
   select a new immutable release. Independent commits allow narrow rollback.

## Fixed tool-parameter deadlines

The user explicitly set **ordinary voice = 30 seconds; coding = 60 seconds**.
Count cumulatively across attempts belonging to one logical request; retries
do not reset the timer. Begin at first tool-call output and include stalls during
parameter generation. Observe first-token wait, loading/prefill and actual tool
execution separately. Repeated fragments/new slots cannot refresh the deadline.
Keep existing token/argument-size limits. Close the stream and invalidate the
attempt at expiry; check generation before dispatch and reject late output.
Do not silently relax the limits for a failing long-code case: report the tradeoff
and stop candidate acceptance. No short global timeout for all task execution.

## Necessary, explicitly approved implementation exceptions

Beyond routing/summary/skills, only generic scope enforcement, schema exposure,
diagnostics, recovery budget and clarification lifecycle may change. No ASR,
TTS, wake, model, email transport or broad harness redesign. CLI and IM state
remain isolated. Scope enforcement is not an OS sandbox and cannot promise
infallible model scene selection or prevent arbitrary native terminal code.

## Required acceptance

Use production plugin combination, CLI toolsets and real voice prefix, with
isolated simulator/email data. Include travel -> home -> general -> coding,
button changes, stale confirmations/callbacks, default no scene instructions,
denied cross-scene skill/tool access, bedroom read with one successful query,
one necessary control clarification, clarification timeout, malformed bridge,
missing finish, repeated slots, cancellation and ambiguous writes. Include
valid long arguments and multi-tool work. Simple questions: one business model
call, no summary/email; title/other auxiliary calls are counted separately.
Complex results: faithful brief speech/full captured email and truthful status.
Measure quality, calls, first speech readiness and total latency against
264348292 and dd99230d9 using identical samples; then human acoustic acceptance.
Stage 1 protocol probes are NOT substitutes for this full conversation gate.
