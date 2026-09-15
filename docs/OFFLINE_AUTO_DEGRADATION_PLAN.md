# Automatic offline degradation plan

Date: 2026-09-15
Status: approved design; not implemented
Target: English-only, screenless local voice demo on the Intel AI Box

## Decision

Keep one normal voice mode. The user never selects an online or offline mode,
and the local Qwen model never decides whether the machine is connected. A
definite transport failure opens a host-owned circuit breaker for the current
voice turn. Network work stops, local work remains available, and the next user
turn may try one network operation again.

This revises the earlier offline proposal for the current generalized voice
runtime. It preserves the useful fail-fast behavior already present in
`general_voice.py`, while closing the latency, coverage and multi-recipient mail
gaps found after the English-only, continuity and full-tool changes.

## Verified current baseline

The installed Box copies of `general_voice.py`, `voice_continuity.py`,
`voice_task_router.py`, `voice_continuity_router.py` and `voice_delivery.py`
match the canonical branch byte for byte.

Current behavior already provides these invariants:

- Simple open-ended advice prefers stable local knowledge.
- A failed `web_search` blocks later `web_search` and `web_extract` calls in
  the same generalized voice turn.
- No successful live evidence means no automatic research email.
- Failure state does not contaminate the next user turn.
- Travel asks for live retrieval only for current prices, schedules,
  availability or status, then falls back conservatively after failure.
- Demo-home and local-media operations are local and remain usable offline.

Five focused tests passed on the current source. An installed-code replay on
the Box injected an immediate search failure: no email was sent and the next
local question returned `120` normally. The failed research turn nevertheless
took 38.03 seconds and produced two visible tool events: the real failed search
and a second model-requested search that the host blocked. Its full-tool request
contained 21 schemas and 13,511 prompt tokens.

The remaining gaps are:

- A `web_search` call may try Brave and then a keyless rescue provider, so one
  model tool call can still contain more than one external connection attempt.
- `web_extract` list-shaped errors, browser failures and messaging failures do
  not open the current search latch consistently.
- The model receives another full-tool iteration after the first failure and
  may announce or request an alternative search before being blocked.
- The configured three-recipient voice email group can make one SMTP connection
  attempt per recipient. With the current 30-second connection timeout, a hard
  outage can create a long sequential wait.
- Coding and external-action requests intentionally return to native Hermes;
  the current CLI loop guard is warning-only and is not an offline breaker.
- `LOCAL_DEMO_REQUIREMENTS.md` still calls offline behavior deferred, while the
  later voice release documents a partial fail-fast implementation.

## Goals

1. Make physical-network loss automatic and invisible to the user.
2. Allow at most one real external connection attempt per voice turn after a
   definite offline transport result.
3. Continue useful local work without claiming current facts were verified.
4. Preserve normal Brave, browser, email, IM and native Hermes behavior when
   the network succeeds.
5. Preserve prompt caching, message-role alternation and the fixed per-session
   tool schema.
6. Keep the policy domain-general. Scenario-specific content remains in skills.
7. Keep all user-facing voice recovery text short and English-only.

## Non-goals

- No `offline voice` command, profile or user-facing mode switch.
- No periodic Internet probe, startup probe or extra request on the successful
  online path.
- No long-lived or persisted offline flag.
- No instruction asking Qwen to classify network state.
- No global removal of web, browser, messaging or terminal tools.
- No mid-conversation toolset or system-prompt mutation.
- No automatic background retry of email or another external action.
- No parsing and blocking arbitrary terminal commands such as `curl`; doing so
  would threaten ordinary local coding. The generalized content path must stop
  before the model can switch to such an alternative after a detected outage.
- IM reconnection and cron delivery recovery are outside this voice-turn plan.

## Failure classification

Add one small pure result classifier with no model tool and no prompt content.
It must accept exceptions, JSON strings, dictionaries and list-shaped tool
results, including per-URL `web_extract` results.

Classify outcomes as follows:

| Class | Examples | Opens offline breaker | Keyless/provider rescue |
|---|---|---:|---:|
| `success` | Valid search results, SMTP acceptance | No | Not needed |
| `transport_unreachable` | DNS failure, network down, no route, refused proxy, connect timeout | Yes | No |
| `remote_transient` | HTTP 429/5xx, service read timeout | No | Existing bounded rescue allowed |
| `configuration` | Missing key, authentication failure, invalid proxy/TLS configuration | No | No physical-offline claim |
| `content_or_policy` | Bad URL, blocked site, empty page, website policy | No | Preserve existing behavior |
| `ambiguous_delivery` | Connection lost after SMTP DATA may have been accepted | No definite retry | Never automatic retry |

String matching is a backward-compatibility fallback. Providers touched by the
implementation should return an additive, bounded `error_code` so the voice
layer does not depend on prose. Unknown errors remain ordinary tool failures and
must not mark the whole device offline.

## Turn-scoped state machine

State is keyed by the active voice session and user turn. It is memory-only:

```text
UNKNOWN
  | successful network result
  +--------------------------> ONLINE_FOR_THIS_TURN
  |
  | definite transport_unreachable
  +--------------------------> OFFLINE_FOR_THIS_TURN

OFFLINE_FOR_THIS_TURN
  | any later network-capability request
  +--------------------------> blocked without I/O

next user turn: state is discarded and starts at UNKNOWN
```

Success need not be cached across turns. A recovered connection therefore works
on the next request without a timer, manual reset or stale session state. A
provider error must not poison unrelated email or browser capability unless it
is classified as a device-level transport failure.

The first implementation should use the existing general-voice plugin and
`TurnContinuation` ownership. Do not add a new model-visible tool. If a small
generic hook enhancement proves unavoidable, it must be additive, opt-in and
validated with a real consumer in the same change.

## Web behavior

For a configured Brave request:

1. Keep the successful request path unchanged.
2. Use separate connect and read budgets. Target a 5-second connection budget
   on the Box while retaining the existing 15-second response budget.
3. Do not invoke keyless rescue after `transport_unreachable`; a second Internet
   provider cannot repair a physically absent network.
4. Preserve the current bounded keyless rescue for an online provider-specific
   rate limit or service error.
5. Feed the normalized outcome to the voice-turn breaker.

For `web_extract` and browser tools, recursively inspect result items. A whole
call whose failures are all `transport_unreachable` opens the breaker. A partial
success retains its verified evidence and records retrieval as incomplete; it
does not become an offline claim.

Once the breaker is open, all later web search, extraction and browser network
calls in that voice turn return one deterministic result without external I/O.
The result must tell the host to finish from stable local knowledge or state
that current information could not be verified. It must not suggest another
provider, browser or guessed URL.

## Fast convergence without cache breakage

Do not remove tool schemas or change the system prompt after failure. That would
invalidate the conversation prefix cache and violate the repository contract.

Instead, after the first definite transport failure in a generalized content or
research turn:

1. Mark the current `TurnContinuation` execution budget exhausted after the
   already-running tool batch completes. Additional calls in the same batch are
   blocked by the breaker before I/O.
2. End the ordinary full-tool loop before a second model-selected search.
3. Finalize with either deterministic text or one bounded side completion that
   has no tool schemas and is not appended as a new conversational turn.
4. For a request explicitly requiring verified current data, return a direct
   failure instead of generating substitute facts.
5. For a task that can still use stable knowledge, ask the local model for a
   conservative offline answer and prepend no fabricated verification claim.

The side completion is the same architectural class as the existing router,
summary and read-only-result recovery calls. It does not rewrite history,
alternate roles, swap the session toolset or mutate the cached system prefix.

Native coding and external-action turns keep their original authorization and
tool behavior. The plugin may block a second known network-capability call after
a definite transport failure, but it must never block local terminal, file,
cron, demo-home or local-media operations. Do not attempt to infer whether an
arbitrary terminal command uses the Internet.

## Local fallback and truthfulness

Host evidence, not model confidence, determines what may be claimed:

- Stable explanation, drafting, summarization, local file work and coding may
  continue locally.
- Live prices, schedules, availability, weather, news and current status are
  unverified when no retrieval succeeds.
- A partial result may use successful sources and must label missing checks.
- Model-written URLs that were not observed in successful current-turn tool
  results remain omitted.
- A failed live-only request is not converted into a detailed email artifact.
- A useful stable artifact may be saved locally even when email is unavailable.

Short English voice outcomes:

- Live-only: `I can't reach live sources, so I can't verify that right now.`
- Stable fallback: answer briefly, then `I couldn't verify current details.`
- Saved mail artifact: `I completed and saved the details locally, but email is unavailable right now.`
- Partial delivery: `The details are saved, but delivery was only partially confirmed.`

The final wording must still pass the existing voice word/sentence limits.

## Multi-recipient email behavior

Move group delivery accounting to per-recipient receipts rather than treating a
comma-separated group as one indivisible send:

1. Preserve accepted recipients as accepted and never duplicate them.
2. Stop the recipient loop after the first definite pre-acceptance transport
   failure; do not wait on the remaining addresses.
3. Record untouched recipients as not attempted, not failed or accepted.
4. A definite connect failure is retriable only after a later explicit user
   request. It must not create a permanent `unconfirmed` dedupe record.
5. A failure after SMTP may have accepted the message remains `unconfirmed` and
   is never automatically retried.
6. If the same turn already opened the offline breaker through search or
   browser, skip SMTP entirely and keep every recipient not attempted.
7. Never perform a background retry. The user can explicitly request delivery
   after connectivity returns; already accepted recipients stay deduplicated.

Use a voice-path SMTP connect target of 8 seconds on the Box. Keep the adapter's
normal response/authentication behavior and do not shorten successful delivery.
The implementation must expose enough structured stage information to
distinguish definite pre-acceptance failure from ambiguous post-send failure.

## Scenario boundaries

- Travel: retain the current stable itinerary path and skill-owned one-search
  rule. Current fares, timetables and availability remain explicitly unverified.
- Demo home: always use the local simulator. Network state is irrelevant.
- Local media: always use the local helper. Network state is irrelevant.
- Local text and coding: continue with local model, terminal and files.
- Local scheduling: create and query local schedules normally; do not claim a
  network delivery channel succeeded while offline.
- New skills: state whether current data is essential and what stable local
  fallback is allowed. Do not reimplement transport detection in each skill.
- IM/typed input: retain native behavior. This plan is activated by the existing
  local generalized voice profile, not by language or topic heuristics.

## Expected implementation footprint

Prefer extending existing edge modules; add no model-visible schema:

- New pure helper, likely `hermes_cli/voice_network.py`: normalized outcome and
  turn-scoped state.
- `hermes_cli/general_voice.py`: initialize/reset state, observe tools, stop the
  continuation and choose truthful finalization.
- `hermes_cli/voice_continuity.py`: retain successful source provenance and pass
  retrieval outcomes to the same state.
- `hermes_cli/voice_delivery.py`: per-recipient receipt semantics and retriable
  pre-acceptance offline state.
- `tools/web_tools.py` and the Brave provider: structured transport result,
  connect/read timeout split and rescue classification.
- Email adapter only if it cannot currently distinguish connection failure from
  ambiguous post-acceptance failure. Any new field must be additive.
- The existing general-voice plugin registration: reuse its hooks; do not create
  a second competing workflow plugin.

Do not change `cli.py`, `run_agent.py`, `agent/conversation_loop.py` or the
system prompt unless the existing continuation/plugin surfaces are proven
insufficient by an executable test. Do not change the active tool list.

## Test plan and release gates

Unit tests must exercise behavior, not read source text:

- Every supported result shape maps to the correct failure class.
- DNS/no-route/connect-timeout opens the breaker; HTTP 429, 5xx, auth, content
  and policy errors do not falsely mark the device offline.
- Brave transport failure performs no keyless rescue; 429 still can.
- A successful online call follows the previous request and result path.
- A failed `web_extract` list opens the breaker only when the whole call is a
  transport failure; partial evidence survives.
- After the breaker opens, later web/browser calls perform zero I/O.
- No second blocked search event or full-tool inference occurs in generalized
  content/research turns.
- Stable local fallback and current-only failure are both truthful.
- Three-recipient email makes at most one SMTP connection during hard outage.
- Successful recipients are not resent; untouched recipients remain eligible
  for an explicit later resend; ambiguous sends remain non-retriable.
- New turns start unknown and can recover immediately.
- Local file, coding, cron, demo-home and local-media tools remain usable.
- Typed CLI and IM isolation remain unchanged.

Integration tests must use a temporary `HERMES_HOME`, real imports and injected
transport failures at the provider/adapter boundary. They must capture mail and
must not require real recipient delivery.

Installed Box acceptance must cover this sequence in one voice session:

1. A current-information request with injected physical-style network failure.
2. A stable local question immediately afterward.
3. Travel planning without current prices.
4. Travel planning that requests a current schedule.
5. A local coding/file task.
6. Demo-home status/control and local media playback routing.
7. A detailed stable artifact while offline, followed by explicit email retry
   after simulated network recovery.
8. A fresh current-information request after recovery using real Brave.

Acceptance invariants:

- At most one external connection attempt per failed voice turn.
- Zero second blocked-search display event after the first transport failure.
- At most one post-failure local text-only completion and zero tool schemas on
  that side completion.
- Web connection failure is bounded to 5 seconds; first SMTP connect failure is
  bounded to 8 seconds.
- No unverified current detail or false delivery claim.
- No automatic email retry and no duplicate accepted recipient.
- Next-turn local response and next-turn online recovery both succeed.
- System prompt and primary session tool-schema hashes remain stable.
- Existing generalized voice, continuity, travel, home, media, approval,
  clarification, wake, ASR/TTS, email, Brave and IM regression suites pass.

Run Python tests through `scripts/run_tests.sh`, never direct `pytest`. Before
Box deployment, commit and push reviewed source, back up every replaced runtime
file and configuration, deploy only matching files, and rerun the installed-code
sequence. Human acoustic acceptance is required after automated tests.

## Rollout and rollback

1. Implement and test the pure classifier and provider behavior in source.
2. Add voice continuation and delivery integration behind the already enabled
   generalized voice workflow; do not add a user mode.
3. Validate the normal online path first, including Brave success and all three
   email recipients with capture-only tests.
4. Validate injected outages and recovery locally.
5. Commit and push the reviewed source.
6. Back up the exact Box files and config, then deploy the narrow overlay.
7. Validate installed code with capture-only mail, then one authorized real
   Brave request. Do not send real mail unless separately authorized.
8. Restart the voice process normally and perform human ASR/TTS acceptance.

Rollback restores the per-file backup and restarts the voice process. The state
format should be additive. Accepted delivery receipts must remain valid across
rollback; new offline/not-attempted states must degrade safely to no automatic
retry rather than duplicate mail.

## Stop conditions

Do not deploy if any of these remain true:

- The implementation changes the session system prompt or tool schemas after a
  failure.
- A successful Brave call is slower or loses results because of the breaker.
- A provider-specific error is mislabeled as physical offline.
- A failed group email can duplicate an already accepted recipient.
- A new task inherits the prior turn's offline state.
- Local coding, files, schedules, home or media are blocked.
- The installed Box needs a manual offline-mode command.

