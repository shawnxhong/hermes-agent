# General English voice workflow release

Date: 2026-09-09. Model: local `qwen3.6-35b-a3b` through OVMS. No cloud LLM
fallback. This release implements the approved correction after the Melbourne
failure recorded in `VOICE_MODEL_FIRST_RESET.md`.

## Resulting boundary

The harness owns the voice channel, not task content. It buffers model output,
speaks a short summary, stores a substantial result, performs truthful email
delivery, and keeps task/recipient continuity. Native Hermes and the local model
own the answer, tools and essential questions for arbitrary English subjects.

The specialized travel execution and its content validators are no longer in
the default path. Travel uses the same native path as other non-coding voice
tasks. The old travel skill and plugin remain available as source/reference but
are disabled in the running configuration. Old travel test records are not
migrated.

Typed CLI and IM input bypass this workflow. Coding, explicit file work and
external actions return to the native harness with its normal permissions.

## Voice behavior

- A new simple question is answered in at most 100 model words, summarized to a
  maximum of 45 spoken English words when needed, and is not auto-emailed.
- Open-ended advice or suggestions stay brief unless the user explicitly asks
  for a plan, report, itinerary, comparison, draft or other detailed artifact.
- A complete detailed request executes immediately. The first detailed result
  or brief-to-detailed expansion is auto-emailed to the configured recipient;
  later explanations and revisions do not silently resend it.
- The model may ask an ordinary question only when an essential missing fact
  prevents a useful or safe answer. Distinct essential questions are possible;
  an exact repeated question after an answer is stopped.
- A new unrelated request starts a new task. Explicit references can return to
  a saved task, revise it, or send its current version. Recipient confirmation
  remains bound to that task and cannot capture an intervening new question.
- The model cannot call `speak`/TTS inside a buffered turn. The host alone plays
  the finalized short response.

## Tool convergence and truthfulness

Simple turns allow four tool calls and one search. Complex turns allow eight
tool calls and three successful search calls; exact read-only retries remain
bounded, side effects are never automatically retried, and one failed
`web_search` disables further search/extract execution in that turn.

If a read-only research loop has successful evidence but does not converge, the
host makes one 45-second, text-only finalization request using only tool results
from the current user turn. If no live source succeeds, the host gives a short
failure statement and sends no automatic email. Unobserved model-written URLs
are omitted; observed source URLs are retained with a verification note rather
than using a content-specific validator to discard the whole answer.

The deployed `providers.custom` entry mirrors the same local OVMS endpoint and
sets `request_timeout_seconds` and `stale_timeout_seconds` to 90. This bounds a
stalled local generation without changing the model, endpoint or thinking mode.

## Acceptance evidence

- 13 focused files: 213 tests passed, zero failed.
- Exact Melbourne/Sydney/October/five-day replay: useful first answer, complete
  detailed second answer, one captured email, unrelated sky question isolated,
  and a later return to the Melbourne task passed.
- Randomized mixed replay: three unrelated detailed artifacts each mailed once;
  three simple questions and a later new question not mailed; saved agenda
  return, revision, explicit resend, invalid-address confirmation and stale
  confirmation isolation all passed.
- Simulated search outage: one real search attempt, immediate blocking of later
  attempts, no unverified email, short truthful response and normal next-turn
  recovery passed.
- Three repeated isolation passes confirmed that Feishu/typed input bypasses
  the workflow and voice coding/action requests return to native execution.

All replay mail was captured, not sent. The checks validate software behavior,
not inbox receipt or microphone/acoustic quality. A new `hermes --cli` process is
required after deployment because its system prompt and plugins are frozen when
the agent starts.

## Deployment and rollback

Before deployment, commit and push the reviewed source. Back up each replaced
runtime file plus `~/.hermes/config.yaml`; never replace or clean the modified
runtime checkout as a whole. Deploy only the five generalized voice modules,
disable `travel-voice`, remove the embedded travel system prompt, add the
mirrored local provider timeout entry, restart the Gateway, then rerun installed
code acceptance. The timestamped backup manifest is the rollback source.
