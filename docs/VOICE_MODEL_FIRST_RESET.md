# Melbourne failure and model-first reset

Date: 2026-09-09. Status: incident recorded; model identity needs confirmation.
Previous release validation does not establish general demo readiness.

## Confirmed incident

User inputs:

1. “I want to go travel to Melbourne. Could you give me some advice on that?”
2. “I will be traveling from Sydney and I will be going there in October this year and I think I have five days”

The first response asked for month, duration and departure city. The second
reported failure to complete a validated itinerary and no email delivery.
The inputs were complete; asking the user to repeat was inappropriate recovery.

Read-only evidence:

- CLI session `20260909_162620_f165ad`.
- Saved travel task `06a9d207f7244f209f6a4f7a9a7cde2c`: destination Melbourne,
  origin Sydney, days 5, travel_month 2026-10, language en; awaiting null,
  no saved body. Context extraction succeeded.
- `~/.hermes/logs/agent.log`, 16:28:03 local time: `_validate_plan` raised
  `ValueError: Do not claim tickets/reservations are unnecessary; exact-date
  requirements have not been verified` in the installed travel plugin.
- The planner permits one repair using the same evidence, then rejects the
  entire itinerary before delivery. This was not an SMTP failure.
- Actual transport searches used fixed amtrak.com/united.com/delta.com/aa.com
  domains; attraction searches used nps.gov/recreation.gov/gov. US-first was
  incorrectly implemented as fixed US-source restrictions for Australia.

The rejected draft is not available in the diagnostic evidence. The keyword
validator can overmatch, but a false positive in this particular draft has NOT
been proven. Do not invent its wording or attribute the failure to ASR.

The previous 219 tests and accepted replays remain evidence only for their
cases. Final repeated travel acceptance used New York/Vancouver/December/four
days, not this Melbourne case. Manual failure reopens behavioral acceptance.

## User-directed sequence

Do not accumulate destination-specific patches or additional keyword rules.

1. Verify and upgrade local Qwen3.6-35B-A3B to the requested
   Qwen3.8-35B-A3B, with thinking disabled; no cloud fallback.
2. Establish a real-model baseline before changing harness behavior.
3. Relax task-specific harness restrictions toward native Hermes behavior;
   keep voice presentation and delivery as general channel-level contracts.

Review fixed geographic domains, travel-specific content vetoes, rigid turn
counts/schemas, routing and tool budgets for removal or simplification after
the model baseline. Use behavioral guidance instead of mandatory scenario
workflows where possible, not a new set of Melbourne rules.

Preserve local inference, brief verbal acknowledgements and spoken summaries,
ASR/TTS follow-up cues, full IM output, conversation continuity, recipient
intent, email deduplication and truthful delivery status. Retain cancellation,
timeouts and repeated-error protection. Mark uncertainty without falsely
claiming verification. Better model capability and non-thinking performance
are hypotheses to measure, not guarantees.

## Model preflight / blocker

Current container `ovms-qwen36` uses `openvino/model_server:latest-gpu`, GPU,
VLM_CB, hermes3 tool parser and the read-only directory
`/home/agentdemo/models/Qwen3.6-35B-A3B-int4-ov`. The local inventory checked this
turn did not locate Qwen3.8 weights.

Official sources checked on 2026-09-09:

- https://github.com/QwenLM/Qwen3.8/blob/main/README.md
- https://huggingface.co/Qwen/models?p=0
- https://huggingface.co/OpenVINO/Qwen3.8-27B-int4-ov

They confirm Qwen3.8-27B but did not establish a public Qwen3.8-35B-A3B artifact.
Ask for the intended model URL/local path; do not silently substitute 27B or
rename old weights. Existing 3.6 chat templates have customized thinking logic;
verify effective non-thinking on native and auxiliary generation paths, not
only a UI setting. Model identity gates the ordered rollout.

No model, runtime config or harness change, service restart or test email has
been performed for this reset.

## Next acceptance

Capture mail before real delivery tests. Compare identical prompts before and
after each stage: exact Melbourne inputs, varied destinations/durations,
unrelated simple questions, writing/research tasks, random topic switches,
references to old answers, recipient changes/confirmations, native coding/action
handoff and IM. Record failed runs too; measure latency, tool calls, spoken
length, evidence quality and delivery outcomes. Exercise ASR/TTS alongside iGPU
inference. Greeting/math probes or repeated identical itineraries do not prove
agent reliability. Preserve rollback artifacts, commit/push reviewed source
before narrow runtime deployment, and do not migrate old travel test records.
