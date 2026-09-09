# Sydney spoken continuation regression — 2026-09-09

Deployed from `37103951b`; per-file backup:
`/home/agentdemo/hermes-ovms-setup/backups/20260909_105601-sydney-ready-repair`.
Installed wake-start ordering and gap settings verified; unrelated configuration
is unchanged. No Gateway/model restart was required.
Installed six-turn full 21-tool replay passed as well
(`/tmp/hermes-general-voice-wkj3g8l4/receipt.json`): fragment recovery, plan,
native explanation and exact-body resend. Plan took 24.46 s; native explanation
28.04 s, so no latency improvement is claimed for the full-context follow-up.

The manual sequence exposed a routing-state failure, not an SMTP failure.
The spoken variant "I will be traveling there ..." was outside the existing
pending-answer normalization. A generic new-task route could ask for optional
budget/accommodation, and an unclear ASR response could replace the active task.
Finally the classifier returned `answer` while the host phase was not
`awaiting_details`, raising `No outstanding task-details question` twice and
ending with the generic workflow failure message.

Corrections:

- Pending travel context carries its domain into routing. Natural continuous
  tense and contracted references bind to the existing trip, and a pending
  travel answer retains the proven travel strategy, not a new generic planner.
- An uncertain answer or short visibly unfinished transcript while awaiting
  details requests a repeat without replacing task/facts or consuming another
  requirements-question budget. Explicit independent tasks still route normally.
- An `answer` label against an existing non-pending task is normalized to a
  follow-up, not treated as a fatal routing-schema failure. This does not grant
  action permissions or infer missing facts.

The exact Sydney first two turns passed a real local-model/full-tool replay
(`/tmp/hermes-general-voice-1piy4wjs/receipt.json`): second turn generated the
seven-day plan from Melbourne in December, no budget question, captured delivery
in 31.84 s. Explanation and exact-body typed resend also passed. No real test
mail was sent. The replay script now includes Sydney and fragment-recovery cases.

160 focused tests passed. The six-turn Sydney fragment-recovery replay also
passed (`/tmp/hermes-general-voice-1_5gd6bq/receipt.json`): incomplete input used
zero model calls, the repeated answer continued the original trip and produced
its plan in 26.12 s, followed by explanation and exact-body resend. Model-written
transport estimates still require factual review; this fixes routing, not all
possible travel factual errors.

User must restart the CLI and begin a new conversation for retesting; old failed
session state is not rewritten. Acoustic gap audibility still needs human QA.
