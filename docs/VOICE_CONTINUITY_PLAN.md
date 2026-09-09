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
pass. Existing travel delivery receipts must continue to deduplicate.

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
legacy travel ownership/receipt adoption in the new coordinator; successful-tool
source provenance and grounding checks; real CLI lifecycle/isolation checks;
reviewed backup/narrow deployment only after these gates, then human ASR/TTS and
actual inbox verification. Do not enable this checkpoint merely because unit
tests pass. Existing production behavior remains in use.
