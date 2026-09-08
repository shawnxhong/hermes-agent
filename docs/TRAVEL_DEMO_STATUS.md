# US-English Travel Demo — 2026-09-08

## Status

Update: the user explicitly requested default activation for manual testing.
Install the candidate into `~/.hermes/skills/travel-concierge/SKILL.md` and preload
its instructions through the existing `agent.system_prompt` configuration, which
is shared by CLI and Gateway. Apply only to travel requests; preserve normal
behavior for other tasks. This supersedes the default-disabled status below,
but does NOT supersede the known acceptance failures. No new hard tool budget,
sampling changes, recipient default or toolset restriction accompanies activation.
The configured prompt is a snapshot: refresh it when the installed skill changes.

Implemented a candidate Hermes skill at
`skills/productivity/travel-concierge/SKILL.md`. It supersedes the Chinese-first
design draft. It is **not demo-ready or enabled by default**: real local Qwen
tests still show tool-budget, factual-grounding and delivery-policy failures.
Do not represent a passing structural check as acceptance of the itinerary.

The skill-creator workflow informed concise instructions, scoped defaults,
explicit delivery failure behavior and real-model forward tests. This is a
Hermes skill, not a Codex installation.

## Intended interaction

English/US-first, while honoring explicit language and destination choices:

1. User: “I would like to visit San Francisco.”
2. Brief familiar sights, then one final question for dates/month, duration and
   departure city. No search or clarify tool on this first turn.
3. User: “In October, for three days, from Seattle.”
4. Brief research; compact complete itinerary and transportation advice.
   Voice: email details to an explicitly supplied recipient, then short speech.
   IM: details directly in chat, no automatic email or local audio.

No booking, fabricated schedules/fares, inferred recipient or cloud LLM.
Default demo recipient has not been configured without user confirmation.

## Ordinary spoken-question follow-up

`hermes_cli/voice_followup.py` adds an opt-in, generic voice follow-up window.
After a genuine ASR turn, if the actually capped spoken response ends in `?`
or `？`, wait for TTS/full-duplex output to finish, beep and capture one answer
for at most 30 seconds. Reuse the same session and voice-input marker. Do not
open for typed/IM input, completed statements, busy or interrupted turns.

Silence ends the window without repeated listening. A bounded 120-second latch
allows the next wake word to resume the question's session even when normal
wake behavior starts a new session. New input cancels the latch. Stop, echo,
session changes and cancellation do not enqueue stale transcripts.

Defaults remain disabled upstream. Host configuration for testing:

```yaml
voice:
  followup:
    enabled: true
    timeout_seconds: 30
    resume_seconds: 120
    playback_timeout_seconds: 120
stt:
  language: ''
  local:
    language: ''
```

Both ASR language hints must be cleared for automatic English/Chinese detection.
Existing Kokoro command already selects `af_maple` for English language runs.
Keep the existing ASR-only verbal acknowledgement and TTS output caps.

Deployed the reviewed voice-code hunks and the above ASR/follow-up configuration
after pushing commit `79ae06f41`. Backup:
`/home/agentdemo/hermes-ovms-setup/backups/20260908_125629-voice-followup/`.
The dirty live checkout's other changes were preserved. No gateway restart or
travel-skill default activation was performed. Restart the interactive CLI to
load new code. Local model, cloud-key policy, tool discovery and sampling settings
were not changed globally by this deployment.

Post-deploy checks: the actual follow-up worker transcribed the generated English
audio file through local Whisper and enqueued a real `_VoiceInputMessage` in the
same session. Only audio capture was substituted with a file. The host health
check passed local OVMS inference, device enumeration and wake/ASR/TTS dependency
checks. Human microphone and speaker end-to-end acceptance remains pending.

## Automated verification

Run focused regression tests through the repository runner:

```sh
scripts/run_tests.sh -j2 tests/cli/test_voice_followup.py tests/cli/test_voice_response_policy.py tests/cli/test_voice_clarify.py tests/cli/test_voice_approval_and_clarify_dedupe.py tests/tools/test_voice_tts_echo_guard.py
```

Run real local-model checks using the host's Python environment:

```sh
/home/agentdemo/.hermes/hermes-agent/venv/bin/python scripts/local-ovms/check_travel_skill.py
/home/agentdemo/.hermes/hermes-agent/venv/bin/python scripts/local-ovms/check_travel_skill.py --surface im
/home/agentdemo/.hermes/hermes-agent/venv/bin/python scripts/local-ovms/check_travel_skill.py --email-failure
```

Each run uses a temporary Hermes home, native skill preloading, only web and
captured email tools, direct schemas (discovery off), local Qwen, temperature 0,
2048 output tokens and at most 8 iterations. These are test settings, not global
production changes. Only Brave credentials are read into the test environment.
Email never leaves the test: valid sends are captured; incorrect arguments fail.
The harness caps real network requests while counting ALL attempts. Its cap is
not a production enforcement mechanism. Receipts are written under `/tmp`.

Observed results:

- Focused voice regressions: 57 passed across five files.
- Real local Kokoro English synthesis → local Whisper automatic-language ASR
  recognized “in October for three days from Seattle.” File recognition took
  about 10.6 seconds including model loading; this is not a microphone test.
- Preloading consistently made the familiar-city first reply short, question-last
  and tool-free in recent runs (about 4–7 seconds on this host).
- One voice run passed structural checks: two searches, one correctly addressed
  captured email, short final reply, second turn about 19 seconds. Manual review
  still found unsupported prices and source names without URLs. Later skill
  revisions removed price estimates, but delivery compliance remained unstable.
- IM stayed text-only and did not email, but attempted four searches instead of
  two; factual links and rail/route claims also require better grounding.
- A simulated-email-failure scenario never reached the sender: the model printed
  the full itinerary instead. This test FAILED; it does not prove failure handling.
- Earlier runs attempted repeated searches, malformed mail calls or omitted mail.
  Do not loosen assertions to hide these failures.

## Remaining acceptance work

Pure skill text does not reliably enforce a small MoE model's tool budgets.
Evaluate a travel-only deterministic budget/tool wrapper before enabling this
workflow by default; preserve the general agent's existing tools and permissions.
Also verify source grounding, partial follow-ups, destination changes, missing
email and failure paths, then actual microphone wake → acknowledgement → question
playback → answer capture → itinerary email → final playback. File-based audio
tests and mocked capture lifecycle tests do not prove room acoustics or inbox
receipt. No new real email has been sent as part of these skill tests.
