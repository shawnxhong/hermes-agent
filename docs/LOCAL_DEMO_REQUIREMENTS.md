# Intel / Lenovo local agent demo requirements

## Current requirement update — 2026-09-09

The user cancelled the proposed Qwen3.8 upgrade: retain local
Qwen3.6-35B-A3B. English-speaking users may ask arbitrary questions and switch
tasks without following a prescribed demo scenario. The original bounded
scenario direction below is historical, not a scope restriction.

Prioritize general native Hermes execution; relax task-specific harness
restrictions rather than add special-case repairs. Skills may guide relevant
tasks but must not force unrelated work into a workflow. Voice constraints
govern presentation and interaction, not which subjects can be answered.
Do not force two turns: answer simple questions directly; ask only essential
missing details; execute complete requests immediately. Keep spoken follow-ups
brief too. Complex voice results use short summaries and detailed email;
simple answers do not require email. IM retains full native text output.

Retain wake acknowledgement, task acknowledgement, first-follow-up explanation,
question-to-tone gap and ASR answer window; continuity across topic changes;
default recipient xiaoheng.hong@intel.com and one-off recipient overrides;
truthful delivery, deduplication, cancellation and necessary permissions.
Normal operation has network/Brave search. Offline strategy remains deferred
pending observed tests; no cloud LLM dependency is acceptable.

This update records a requirements review, not a runtime change or acceptance
claim. See VOICE_MODEL_FIRST_RESET.md for the Melbourne incident. Any earlier
proposal to upgrade Qwen3.8 before harness work is superseded.

User briefing: 2026-09-07. This is the working baseline for future scenario
skills and acceptance tests, not a claim that live voice acceptance is complete.

## Purpose and deployment

Demonstrate to Intel and Lenovo management that Intel iGPU hardware can run a
local LLM agent while also handling ASR and TTS. Final inference is exclusively
Hermes + local Qwen3.6-35B-A3B through OVMS; no cloud LLM API key is available.
Email and IM remain network services; local inference does not mean offline
mail delivery. Develop and validate skills against this actual local model.

## Voice is the primary interface

- The demo uses no screen or keyboard. All user-facing recovery paths must be
  usable through ASR and TTS.
- On receiving an ASR task, give one immediate verbal acknowledgement before
  substantive work so the user knows the request was received.
- Minimize clarification by using reasonable task-specific defaults. Ask only
  when missing information prevents correct execution. When clarification is
  required, speak it and accept the answer via ASR without another wake word.
- Minimize unnecessary permission prompts. Required permission/approval must
  remain usable through ASR/TTS; do not bypass necessary safety boundaries.
- Keep final spoken responses short. Preserve existing caps: three sentences,
  240 Chinese/mixed characters or 100 English words. Actual playback duration
  remains a live acceptance concern; no additional numeric duration was set.
- For complex scenario tasks, deliver detail by email and speak a short result.
  Report delivery success only after the mail tool confirms success; explain
  failure briefly and avoid repeated identical sends.

## IM is the backup interface

Retain Feishu and other IM integration for recovery when voice fails. IM input
uses ordinary text responses, including detailed content directly in IM; it
must not activate local ASR/TTS or inherit voice brevity/email-delivery rules.

## Scenario skill direction

Define a bounded set of specific demo scenarios. Skills should guide the local
MoE through a repeatable workflow rather than leave every task to spontaneous
planning. Each scenario should define triggers, defaults, required information,
tool/script steps, brief voice output, detailed email content, and error paths.
Reuse existing voice, clarification, approval, email dedupe, and platform
isolation mechanisms. Put deterministic work into scripts where helpful.

The user will supply the full application background and actual scenarios
before skill implementation or live debugging begins. Do not invent a scenario
set from this briefing alone. Later acceptance must include real microphone
wake-up, acknowledgement, work, voice follow-up/approval where needed, email
delivery, brief final playback, and normal IM behavior on the same hardware.
