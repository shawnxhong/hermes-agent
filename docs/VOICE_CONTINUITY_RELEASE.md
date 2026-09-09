# Voice continuity release validation — 2026-09-09

## Scope and status

Release candidate for the local Qwen3.6-35B-A3B / OVMS voice demo. No cloud
model, new agent or memory-model dependency. Legacy travel test data is not
migrated or deleted. Pre-deployment checks have passed; live enablement and
post-deployment checks are recorded separately below when completed.

The coordinator saves brief answers as well as detailed results; scopes references
to session-owned topics and immutable result versions; consumes recipient/yes/no
interactions once; preserves new-task versus follow-up delivery rules. Native
coding/actions, IM and unprompted typed requests retain their original harness.

## Completed checks

- Source and narrow live-runtime overlay: **219 tests passed each**, 12 files.
  Coverage includes real CLI wake/new-session/voice-exit handlers (external audio
  mocked), idle wake beyond the old 120-second timeout, typed/IM isolation,
  native action pass-through, original cue timing and delivery/continuation rules.
- Shuffled mixed-topic native-model sequences: seeds 1/2/3, 39 turns, passed.
  Meeting, memo, purchasing criteria, arithmetic, language and science, then
  return/revise/send an earlier topic and interrupt a pending address confirmation.
  See `VOICE_CONTINUITY_PLAN.md` for the three source receipts.
- Interrupted travel sequence: three passes, 21 turns total. Receipts:
  `/tmp/hermes-general-voice-c__06bfd/receipt.json`,
  `/tmp/hermes-general-voice-9yf2i54h/receipt.json`,
  `/tmp/hermes-general-voice-5er0z40a/receipt.json`.
- Ambiguity / recipient fragment / pause / stale yes / cancellation: three
  10-turn passes. Receipts `f9phal44`, `_5_6l018`, `g0ewuls2` under
  `/tmp/hermes-general-voice-<id>/receipt.json`. The chosen engineering agenda
  was sent, not the more recent marketing agenda. Test mail is captured.
- SMTP failure, truncated generation and failed search: three native-model
  sequences each passed. No false success, no automatic SMTP retry, no report
  email after failed generation/search, unrelated next-turn arithmetic succeeds.
  SMTP receipts: `gqm4zrh9`, `lr7fwder`, `cym354cw`; generation: `55yl1m0m`,
  `ag0n79w1`, `27q9ljkc`; search includes `y4jdvhpk`, `gpo_9v7l`.
- Actual live email adapter: one real message to `xiaoheng.hong@intel.com`, marker
  `HERMES-CONTINUITY-CHECK-20260909-065143`, adapter returned success. The user
  explicitly confirmed inbox receipt. This is distinct from captured replay mail.
- Actual local Kokoro-ZH TTS to local faster-whisper large-v3-turbo ASR file loop:
  188874-byte audio, 2.50 seconds synthesis, 13.51 seconds transcription, exact
  transcript: “The engineering agenda is ready. You can ask another question.”
  This ran alongside local OVMS work. No room microphone/speaker listening test
  is implied by a file loop; prior human audio/cue acceptance is retained.
- Merged installed-runtime overlay with the live 21-tool schema: mixed seed 5,
  13 turns passed, `/tmp/hermes-general-voice-d1rv9vij/receipt.json`.
  Ordinary source replays use their native 20-tool schema. Both preserve their
  own system-prompt/tool-schema hashes throughout each sequence.

## Defects found during validation

Fixed transport-only redirects becoming new content tasks, recipient questions
being treated as content ambiguity, explicit unselected-result requests losing
their pending delivery, and attempted sends of a missing result regenerating a
different substitute. Failed/incomplete output is not an emailable artifact.

A real Seoul replay also exposed cross-entity factual blending. Successful tool
URLs are now result provenance, never URLs invented in the answer. Revisions
retain their own parent evidence plus new evidence. Detailed research has a
per-entity attribution/verification requirement; unsupported specifics must be
omitted or marked unverified. A brief names-only request does not need per-entry
research. Generated prose is not treated as evidence for new factual details.
The existing summary request also validates researched claims against retrieved
excerpts; no additional model round-trip is added. Missing/unobserved citations,
rejected grounding or an unavailable validator produce labelled extractive source
notes, not an unchecked synthesized report. Without usable evidence no new email
is sent. These notes are deliberately distinguished from a verified full report.

Real native-routing checks also caught code requests being intercepted and a
first attempt at an overly broad native category catching ordinary email/report
delivery. The final router separates content/research/coding/action first, with
explicit contrasts. Coding/actions return the original request to the native
harness. Three repetitions of IM/typed zero-call isolation and nine real local
code/file routing cases passed. No requested file was actually created in this
read-only isolation check.

Short numbered lists previously triggered an unnecessary summary validator, which
could reject an actual answer as incomplete. Sequential list markers can now be
converted to spoken separators without another model call; original content is
still saved. Tests cover this path.

The summary validator now receives the actual task request. Instructions addressed
to a draft's intended readers (for example, a welcome memo asking employees to
confirm a start date) are not mistaken for a clarification to the current user.
Native/keyboard hand-off releases the selected content pointer without deleting
saved topics, so subsequent native yes/no replies are not intercepted. Addresses
being discussed as task data do not override the default delivery recipient.

Unfinished travel plans resume the existing strategy after a topic interruption,
even if the router labels that return expand/revise. Initial no-email intent is
preserved when the user supplies dates in a later turn.

Failed runs, including `gyyttf60`, `iynpsum1`, `q4uxgld3`, `klfkh4uq`,
`y5z8947c`, `lvu71m9e`, `17spbzhx`, `mpca9csk` and `pt_qj3rs`, are not
acceptance passes. Earlier three Seoul
workflow-only passes preceded the stronger provenance/grounding checks and are
not substitutes for final candidate verification.

## Limits and deployment procedure

Final staged Seoul acceptance: three consecutive nine-turn passes with the
source-URL provenance assertion enabled:
`/tmp/hermes-general-voice-zfzuz5ry/receipt.json`,
`/tmp/hermes-general-voice-78_wxnti/receipt.json`,
`/tmp/hermes-general-voice-f49v6r4q/receipt.json`.
All three detailed research turns used the explicitly labelled source-notes
fallback, not a claim of a fully verified synthesized restaurant guide. Each sent
one captured default email and one identical captured one-off QQ email; repeated
confirmation and old-topic resend reused the receipt without duplicate submission.
The route/schema/native-tool isolation checks stayed intact.

Final staged ambiguity/cancel passes: `jbg8di1q`, `ruxt0f5s`, `g0vav1j_`.
Final staged interrupted-travel passes: `a9j382w7`, `m1kc7vdc`, `i9pdl_hr`.
Final staged mixed seed 6: `rwh8xktb` (13 turns). This mixed run overlapped another
model replay, so its 66.21-second maximum is not a single-session latency benchmark.
These IDs are `/tmp/hermes-general-voice-<id>/receipt.json`.

Final staged failure repeats (three passes each): SMTP `bsy14_95`, `_mqlly71`,
`au3r82nr`; generation truncation `rbgn0qna`, `6_a57wds`, `8hdp1jac`; search
failure `17r4nvcj`, `1gcrpne5`, `zdn_vs6r`. Each unrelated recovery turn succeeded.
The earlier `pt_qj3rs` run did not reach its SMTP fault because a complete welcome
memo was misclassified by the old validator; it was fixed and is not counted.

Finite replay coverage does not guarantee arbitrary natural-language routing or
all factual claims. Successful source retrieval/citation is not a certification
that a restaurant's hours, prices or availability are current. Human inspection
of sampled research content supplements automated source-URL provenance checks.
Native schemas remain available; replay guards block external writes. Tests do
not claim to have executed every tool's real-world side effects.

Before deployment: push source/tests/docs; back up each destination file and
config; merge only the three reviewed CLI lifecycle hunks and the configuration
default into the dirty live installation, plus the reviewed voice modules and
travel plugin. Enable only `voice_delivery.continuity.enabled`; preserve model,
credentials, permissions, IM and unrelated local modifications.

After deployment: verify exact file hashes, run installed-code mixed replay,
restart/check Gateway and check OVMS. Do not kill an existing interactive CLI;
restart it normally to load the release. A new-session human voice smoke test is
still the check for actual microphone/room acoustics and perceived cue timing.

Rollback: disable `voice_delivery.continuity.enabled` for new processes; restart
CLI/Gateway. If code rollback is needed, restore only the per-file backup targets.
Keep the additive state tables and test records; no destructive database reset.
