# Intel / Lenovo local agent demo requirements

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
