# Travel Concierge Demo — 2026-09-10

## Current status

The active design is a scenario skill over the domain-general voice workflow:

- Canonical skill: `skills/productivity/travel-concierge/SKILL.md`
- Skill version: `0.2.0`
- Voice workflow: `general-voice`
- Local model: `custom / qwen3.6-35b-a3b`
- Default configured recipient: `xiaoheng.hong@intel.com`

The travel skill remains automatically discoverable. It is not copied into
`agent.system_prompt`, and the old `travel-voice` plugin remains disabled.
No travel rule was added to the generic Hermes harness. The domain-general voice
contract now explicitly defers to any loaded scenario skill's staged intake and
output contract. Its false-delivery-claim cleanup also preserves original
newlines so Markdown and other formatted deliverables survive plain-text email.
Because the local Qwen model does not reliably select a skill from the full
automatic index, `hermes-mode voice --run` preloads `travel-concierge` through
Hermes' native `--skills` option. The skill explicitly exits for unrelated
requests; a cross-task replay verifies ordinary tasks remain ordinary.

The skill-creator workflow informed the narrow trigger, staged behavior, surface
separation, and behavioral validation. This is a Hermes skill, not a Codex skill
installation.

## Interaction contract

For a new request missing either duration or departure city:

1. Give a tool-free destination overview naming two or three familiar features.
2. Ask only for the missing duration and departure city in one ordinary final
   question, using at most two sentences and 45 English words.
3. Do not ask for month, exact date, budget, hotel class, food, or party size.

Once destination, duration, and departure city are known:

1. Do not repeat or extend the questionnaire.
2. Produce a title, assumptions, compact transport guidance, a Markdown
   day-by-day itinerary table, and short reservation/uncertainty notes. Every
   table row must be on its own line.
3. Cover every requested day for 1–14-day trips and keep each day geographically
   coherent. Exact date is optional; without it, label the plan season-neutral
   and leave live schedule/availability unverified.
4. Prefer stable knowledge and low latency for an ordinary season-neutral
   itinerary. Search is most useful when the user explicitly requests current
   prices, schedules, opening status, availability, or date-specific transport.
   Prefer focused official-source lookup and stop after a failed result.

A first request that already supplies destination, duration, and origin skips
the question and goes directly to the tables.

## Surface behavior

Buffered local voice returns the complete table to the `general-voice` host.
The model does not call email or TTS and does not claim delivery. The host stores
the complete response, submits it to the configured or user-confirmed recipient,
then speaks only a short summary plus its receipt-derived delivery status.

IM and ordinary typed chat receive the full tables directly. They do not
automatically email or play local audio. An explicit IM email request retains
the normal native message-tool and permission behavior.

## Automated verification

Run repository validation:

```sh
scripts/run_tests.sh tests/cli/test_general_voice.py \
  tests/cli/test_voice_continuity.py \
  tests/skills/test_authoring_standards.py
```

The repository authoring test is the release gate. The generic skill-creator
`quick_validate.py` currently rejects `version`, `author`, and `platforms`,
while this repository requires those fields, so its frontmatter result is
advisory until the two schemas converge.

Run real local-model checks in isolated temporary Hermes homes:

```sh
/home/agentdemo/.hermes/hermes-agent/venv/bin/python scripts/local-ovms/check_travel_skill.py
/home/agentdemo/.hermes/hermes-agent/venv/bin/python scripts/local-ovms/check_travel_skill.py --surface im
/home/agentdemo/.hermes/hermes-agent/venv/bin/python scripts/local-ovms/check_travel_skill.py --complete-request
/home/agentdemo/.hermes/hermes-agent/venv/bin/python scripts/local-ovms/check_travel_skill.py --email-failure
/home/agentdemo/.hermes/hermes-agent/venv/bin/python scripts/local-ovms/check_travel_skill.py --cross-task
```

These checks preload the canonical skill, use the real local Qwen endpoint and
the real `general-voice` continuation, and capture host email in memory. They
never send a real message. Assertions cover tool-free first stage, no redundant
date/budget question, complete table shape/day coverage, model-owned mail/TTS
avoidance, short spoken result, truthful failure status, and IM isolation.
Receipts are written under `/tmp/hermes-travel-skill-check-*/receipt.json`.

Latest results:

- 1,253 focused repository tests passed.
- Two-stage buffered voice passed; the first turn was tool-free and the second
  retained one complete five-day table for a single captured host email:
  `/tmp/hermes-travel-skill-check-b4trbc0z/receipt.json`.
- IM passed with a directly renderable table and no automatic email:
  `/tmp/hermes-travel-skill-check-xfiud4bg/receipt.json`.
- A complete first request skipped intake and produced the captured table:
  `/tmp/hermes-travel-skill-check-6w8fw1hz/receipt.json`.
- Simulated SMTP failure preserved the detail and reported only unconfirmed
  delivery: `/tmp/hermes-travel-skill-check-u4b5hckp/receipt.json`.
- A same-session switch from the completed trip to ordinary arithmetic returned
  only `4`, with no tool or mail call:
  `/tmp/hermes-travel-skill-check-i3tckv__/receipt.json`.

These are structural and workflow checks, not a claim that every unsourced travel
fact is current. Date-sensitive details still require retrieval or user review.

Human acceptance still requires a fresh CLI session for wake → acknowledgement
→ destination question → direct answer capture → short summary/TTS. Inbox receipt
must be confirmed by the user; SMTP acceptance alone does not prove delivery.

## Historical implementation

`docs/TRAVEL_VOICE_WORKFLOW.md` and
`scripts/local-ovms/plugins/travel-voice/` document the earlier travel-only
interceptor. They remain as reference and rollback material but are not enabled
in the current demo. The overlapping legacy `travel-planning` installation is
retired to prevent ambiguous automatic selection. Old test trip records are
intentionally not migrated.
