# Voice continuity implementation contract

Approved 2026-09-09. Local voice and direct replies to its pending prompts only;
ordinary typed CLI, coding, IM and native action approvals remain unchanged.
Same-session tasks and results survive topic switches and idle wake intervals.
Explicit new session, voice exit or process exit ends automatic continuity.

Separate content, interaction and delivery: save every successful answer, retain
immutable main/revision/explanation results, select only session-owned references,
and expose bounded recent-topic/recent-turn context to one local structured router.
Never rewrite cached system/tool schemas or historical messages. No new agent,
memory model, embedding service or cloud dependency.

New complex tasks retain short speech plus automatic detail email. Explanation,
expansion and modification save updates but email only when explicitly requested.
Default destination is configured xiaoheng.hong@intel.com. Address overrides bind
only one delivery and retries. Pending confirmations bind an exact task, result
version and candidate value; yes/no cannot authorize arbitrary native actions.
Consume confirmations once, reject stale workers, preserve ambiguous SMTP receipts.
Unclear answers keep state; after one repeat prompt pause instead of looping.

Implementation stages: transactional store/migration; typed routing and native
execution integration; travel strategy/voice lifecycle adapters; cross-domain
full-tool real-model acceptance, commit/push, narrow backed-up deployment and
human speech/inbox acceptance. Keep continuity disabled until all release gates
pass. Per user update, legacy travel records are disposable test data and migration
is not a release requirement. No historical data is deleted by this change.

Release cases (each real-model sequence three times): Seoul recommendations to
details/default email; invalid address/yes/repeated yes; one-off redirect; travel,
shopping, meeting and drafting; return to earlier topic; ambiguous references;
fragment/no/cancel/stale result; >120-second wake; explicit session reset; search,
generation and SMTP failures; IM/coding/typed isolation. No wrong-task mail,
duplicate mail, repeated confirmation loop or raw long TTS is acceptable.

## Implementation checkpoint (2026-09-09, not a release)

Implemented behind `voice_delivery.continuity.enabled: false`: additive session
topic/result/pending-interaction storage; local structured routing; shared native
execution finalizer; one-shot recipient confirmation and receipt reuse; typed
pending replies; travel content-only adapter; idle-wake session retention.
Brief and detailed artifacts are distinguished so a details request cannot simply
email an existing short answer. No live deployment/configuration change yet.

One corrected captured-mail Seoul replay passed at
`/tmp/hermes-general-voice-v2srpsnh/receipt.json` (local, ephemeral test artifact).
It covers enrichment, default email, malformed address/yes/repeated yes, typed
one-off recipient, unrelated RSVP question and return to the original report.
Earlier replays exposed routing failures and one insufficient content assertion;
they are NOT passing acceptance evidence. The test subsequently permits read-only
native tool discovery as well as search; repeat acceptance on that exact checker.

Remaining release work: three repeats of each cross-domain release sequence;
successful-tool
source provenance and grounding checks; real CLI lifecycle/isolation checks;
reviewed backup/narrow deployment only after these gates, then human ASR/TTS and
actual inbox verification. Do not enable this checkpoint merely because unit
tests pass. Existing production behavior remains in use.

## Cross-domain verification update

User priority: no migration of disposable legacy travel test data. Focus on
unannounced new questions/tasks, interruptions, and subsequent old-topic returns.

Added reproducibly shuffled mixed-topic native-harness replays covering meeting
agendas, office memos, commuting purchase criteria, arithmetic, science and language
questions. Each sequence then returns to, revises and emails the original agenda,
opens an address confirmation, interrupts it with a new question and rejects a
stale yes. A separate travel replay interrupts a pending trip question, returns
with dates, inserts an unrelated memo, then returns to the itinerary.

Early real-model runs found genuine failures despite green unit tests:
new documents misclassified as edits to an unrelated document; references after
a generic email acknowledgement selecting an earlier unrelated question; and
initial travel email intent lost after a topic interruption. These failed runs
and earlier weak assertions do not count as acceptance passes.

Fixes are domain-independent except the existing travel initial-delivery policy:
classify independent/follow-up intent before selecting content; use descriptive
session-owned handles instead of opaque IDs; reject nonexistent result versions;
attach the actual selected task to each recorded turn, including delivery-only
acknowledgements; retain initial delivery intent when resuming unfinished work.
No added cloud model, memory tool, mutable native system prompt or toolset filter.

All real-model mail in these checks is captured, never sent to an actual inbox.
Finite seeded tests are regression evidence, not a guarantee for arbitrary inputs.

Final checkpoint evidence on the updated code:

- 144 tests passed across continuity router/store/coordinator, original general
  voice/delivery, travel strategy, native continuation, and voice follow-up tests.
- Mixed seeds 1/2/3: 39/39 turns passed all strengthened assertions. Receipts:
  `/tmp/hermes-general-voice-pf40thm7/receipt.json`,
  `/tmp/hermes-general-voice-epriuhuw/receipt.json`,
  `/tmp/hermes-general-voice-i1eg9b46/receipt.json`.
  Median turn time 5.57 seconds, maximum 27.23 seconds; maximum spoken length
  52 English words. These timings exclude real ASR capture/TTS playback.
- Interrupted travel mixed sequence: 7/7 turns passed, including completing and
  automatically delivering the original itinerary after an unrelated question:
  `/tmp/hermes-general-voice-c__06bfd/receipt.json`.
- System prompt and native tool schema hashes stayed constant within each replay.
  External writes were blocked by the test harness, and all emails were captured.

This is source-checkout verification, not deployment approval. The travel mixed
sequence has one pass, not three; the remaining release gates and real installed
ASR/TTS/inbox validation have not been represented as complete. Live continuity
remains disabled and no old test records were deleted.

## Release-validation follow-through

See `VOICE_CONTINUITY_RELEASE.md` for the remaining-gate evidence and defects
found in the real installed-runtime overlay, including native/typed ownership,
bounded recipient repair, interrupted travel completion, and research grounding
fallback. The user has confirmed receipt of the real validation email; local
TTS-to-ASR file transcription also succeeded. Source and merged-runtime regression
coverage is now 219 passing tests. The live flag is not changed by this document;
deployment status and rollback targets are recorded in the release report.
