# Controlled local travel voice workflow

Implemented for the user's US/English-first, screenless Intel iGPU demo.
The default travel recipient is explicitly authorized as
`xiaoheng.hong@intel.com`. SMTP acceptance is not inbox receipt.

## Behavior

- First vague request: one local structured extraction/orientation call, a
  short final question, no search or email. Existing verbal acknowledgement
  and ordinary-question ASR follow-up remain in the CLI.
- Next answer: saved destination plus supplied origin, month and duration.
  The host resolves “next month”, including year rollover, before searching.
- At most two native searches (three results each); the host builds the route
  in origin-to-destination order and prefers primary transport/government sites.
  Search failure stops subsequent queries. No model-driven search loop, browser,
  extraction or delegation fallback exists on this intercepted path.
- Structured generation returns separate `spoken_summary` and `detailed_plan`.
  The plugin validates complete day coverage, sequential days, 2–3 activities
  per day, exact duplicate activities and required transportation/reservation
  sections. It does not pretend schema validation proves every travel fact.
- Host renders detail with canonical trip facts and actual research URLs,
  submits one email, then returns only the short summary plus truthful status.
  Recipient overrides must come from the user's actual text, never the model.
  Explicit “don't email” is respected. Missing mailbox answers reuse the plan.
- Summary: at most 60 English words/two sentences before the host adds status.
  Existing three-sentence/100-word final TTS cap remains. Raw JSON and detailed
  itinerary never stream to the screen or TTS on this path.
- Non-streaming local generation requires a complete `finish_reason=stop`
  response. Malformed/truncated drafts are not emailed. At most three local
  model calls per turn, including bounded repairs; each HTTP call has a 60-second
  timeout and SDK retries disabled. Extraction uses 1024 tokens; full detail uses
  4096. The ordinary model output budget is unchanged.
- Durable SQLite records preserve trip facts and reserve each email before SMTP.
  Identical submissions (session, recipient, complete body) are not automatically
  retried, including pending/ambiguous results. Cancellation/stale sessions are
  checked before submission. A started SMTP submission cannot be recalled.

Only genuine CLI voice turns are intercepted. Typed CLI and IM input retain
normal Hermes behavior: full text, no automatic travel email or TTS. The lexical
travel router covers travel/trip/itinerary/vacation/holiday/visit, travel-oriented
“plan/spend ... days/weeks”, Chinese travel terms and answers to a pending trip
question. Saved-state extraction can decline an unrelated follow-up. It is not
a universal intent classifier; ambiguous tasks outside these triggers still
use normal Hermes. This demo currently validates 1–14-day plans.

## Implementation and configuration

Canonical plugin: `scripts/local-ovms/plugins/travel-voice/`.
Install under `$HERMES_HOME/plugins/travel-voice/` and opt in:

```yaml
plugins:
  enabled: [travel-voice]  # merge with other existing enabled plugins
travel_voice:
  enabled: true
  default_recipient: xiaoheng.hong@intel.com
```

Keep the installed travel skill and its `agent.system_prompt` preload snapshot
in sync. No cloud provider, proxy change, generic toolset change or permission
bypass is introduced. State is under `$HERMES_HOME/cache/travel-voice/state.sqlite`.
Disabling `travel_voice.enabled` returns routing to the normal skill-guided agent.

The narrow core extension is a native-only synchronous `run_turn_workflow` hook.
It runs once before the ordinary model/tool loop, with trusted `input_modality`
from CLI. Native plugins return None to decline or `{handled: true,
final_response: str, api_calls: int, failed?: bool}`. First handler wins; later
handlers cannot repeat side effects. Exceptions fail closed. Handlers own bounded
I/O and cancellation. The core still uses its normal finalizer, history and
SessionDB persistence; it does not change the main agent's cached prompt or tools.
The hook is not a shell-hook surface and does not run under an abandon-on-timeout
wrapper that could leave an email send racing a fallback response.

Schema request format follows the [OVMS structured-output documentation](https://docs.openvino.ai/2025/model-server/ovms_structured_output.html).
The running local server accepted these requests in real tests; host validation
still runs even if a server does not enforce a requested schema.

## Validation

101 focused regressions passed: workflow routing, original voice follow-up,
acknowledgement/TTS policy, email dedupe, plugin compatibility, hook exceptions,
same-session alternation and real SessionDB persistence.

Real local-Qwen native-plugin replays in isolated Hermes homes:

- User's exact two-turn San Francisco / seven days / next month / New York
  example: tool-free first question (~2.7 s), two searches and complete seven-day
  email, short final response (~23 s second turn). No raw streaming deltas.
- Simulated mail failure: one captured attempt, short “not confirmed” status,
  retained detail and no resend (~23.4 s).
- Complete request + simulated unavailable search: one search attempt, complete
  uncertainty-labelled plan and short summary (~18.5 s); no retry loop.
- One real SMTP test submitted successfully to the default recipient with
  `HERMES-TRAVEL-WORKFLOW-TEST` in the first line. Receipt:
  `/tmp/hermes-workflow-check-s0pbjb_9/receipt.json`.

Run `scripts/local-ovms/check_travel_workflow.py` with the host Hermes Python.
Default captures email. Options: `--email-failure`, `--search-failure`,
`--complete-request`, `--live-code`. `--send-real` explicitly submits ONE real
test email; do not use it for repeated automated regressions.

Earlier failed replays revealed non-JSON extraction and a complete-request
router miss; both became fixes before deployment. These tests are not claims of
universal itinerary accuracy or human microphone/speaker acceptance. The actual
room's ASR/acknowledgement/follow-up/TTS chain still needs the user's manual test.
