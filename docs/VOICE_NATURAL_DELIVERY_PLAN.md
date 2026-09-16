# Unified natural voice delivery

Approved 2026-09-16, laptop only. Keep one continuity router; remove the legacy
task router and its execution branch. Route task/reference, execution and
deliverable intent separately, without scene-specific keyword overrides.
Short natural replies pass through. Detailed deliverables retain their full
body and receive a direct spoken reply plus host-owned asynchronous email.
Substantive revisions auto-email; explanations do not resend. Explicit no-email
wins. Native coding/actions retain permissions and tools; do not automatically
export source files, code blocks or raw tool output. IM/keyboard unchanged.

Reduce context to current task, two recent turns and compact result references;
load selected result once. No recipient injected into content generation.
Use one spoken-body budget of 60 words/three sentences, plus one independent
host delivery-status sentence. Compression is an actual reply to the user, not
narration about the assistant. Preserve streamed first-sentence delivery and
safe partial-failure/cancellation behavior. Ordinary yes/no and polite questions
are not implicit email confirmations or blocking requirements slots.

Test router references/ownership, generic paraphrases, short-answer identity,
long-answer compression, detailed artifacts/revisions, no-email, pending
recipients, offline/interruption, native execution and keyboard/IM isolation.
Run isolated real-model cases with fake email transport before publication.
Use task worktree/PR, exact release, ops receipt, retained rollback release.
Never restart the user's active CLI or deploy to Boxes.

## Implementation and verification

Task branch/worktree: work/laptop/voice-natural, based on 264348292.
`voice_continuity_router` is the sole router. The deprecated
`voice_delivery.continuity.enabled` value no longer chooses an implementation;
the top-level `voice_delivery.enabled` remains the opt-in switch. No configuration
migration or saved-result migration is required.

`voice_presentation` owns brief-answer identity and one-pass compression. CLI
native coding/actions also use this boundary, without adding a continuation or
changing tool permissions. Presentation/storage failure cannot change native
execution success. Automatic export uses final prose only, excludes recognized
source/code blocks and copied raw tool output, and never attaches local files.
Existing content continuations retain execution budgets and offline safeguards.
Question replies are recorded as conversation, not new blocking slots; existing
persisted pending slots remain consumable. Recipient/reference confirmations
remain explicit host-owned operations.

The router receives two recent turns and compact topic handles, no saved bodies
or generated topic summaries. Generation receives selected content once, no
default recipient and no duplicate current request. Scene-specific intake is
left to skills. Main native conversation history is not rewritten.

Tests migrate legacy behavioral contracts to the unified entry, adding native
presentation failure/cancellation, code/raw-output exclusion and revision email
coverage. `scripts/local-ovms/check_natural_voice.py` exercises the real local
model and native loop in a temporary profile, with captured email only. Initial
live testing exposed document-narration in compression despite passing unit
tests; the compression prompt now explicitly treats the result as the assistant's
own completed work, and live acceptance checks for the observed narration forms.
Streaming stress testing also exposed invented later-day activities from a
repetitive single-day input. Compression now explicitly forbids adding steps or
facts absent from the result. Follow-up live outputs retained first-day scope
and explicitly preserved the unavailability of later-day details. An initial
test incorrectly rejected the phrase "subsequent days remain unavailable";
that is preserved uncertainty, not invented later-day activities.
The final replay also returned `answer` for an explanatory follow-up. A generic
versioning invariant now stores follow-up answers as supplementary content:
only explicit revisions/expansions replace the saved deliverable. The live
acceptance checks that the full emailed report remains selected after explanation.

Release identity, final test counts, local receipt path, unchanged service facts
and rollback are recorded in the private ops repository. Human acoustic and
open-ended naturalness acceptance remain necessary; no rule can guarantee every
model response. Active CLI sessions must be relaunched normally to load code.
