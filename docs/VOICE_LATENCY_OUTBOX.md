# Voice latency: durable asynchronous email

## Evidence (laptop, 2026-09-16)

The Tokyo voice turn spent 7.8 seconds between recording end and transcript,
then about 3 seconds preparing/playing its fixed acknowledgement. Its main
model call took 6.3 seconds. Finalization continued for another 122.9 seconds.
Four recipient receipts were unconfirmed. The 8-second SMTP override affected
`plugins.platforms.email.adapter`, but the registered sender ran the same file
as `hermes_plugins.email_platform.adapter` with its independent 30-second
default. Four sequential 30-second failures are consistent with this gap;
the old logs did not measure each individual send.

The keyboard comparison took about 55 seconds overall, with visible streaming.
It was not an equivalent request/session benchmark. No claim of a measured
new real-world voice latency is made before human acceptance.

## Behavior

- SMTP remains authenticated TCP/TLS, not UDP. Speech does not wait for SMTP
  acceptance or inbox delivery. Only a durable local enqueue precedes the
  spoken status: `The details are queued for email delivery.`
- `cache/voice-delivery/outbox.sqlite` under HERMES_HOME stores private immutable
  body/recipient snapshots. A detached Python worker drains under an exclusive
  process lock, preserving the existing per-recipient deduplication ledger.
- Accepted is SMTP submission, not proof of inbox receipt. Queued, accepted,
  offline, partial, and unconfirmed remain distinct. Worker launch failure says
  saved locally instead of claiming that sending started.
- The worker survives normal CLI exit. Untouched queued work resumes on a
  later voice turn after a machine/process restart. In-flight crash results
  become unconfirmed and are not automatically resent.
- Definite offline failure stops the recipient group. Only an explicit later
  submission retries offline/partial jobs; already accepted or ambiguous
  individual recipients remain protected by the existing ledger.
- Once queued, a request remains authorized even if the user changes topics.
  No new queue-cancellation UI is introduced in this change.
- Keyboard/IM native workflows, model prompts and tool budgets are unchanged.

## Other speed changes

The SMTP context lives in one shared module so plugin aliases see the same
8-second voice timeout; non-voice standalone mail retains its 30-second default.
This is a socket-operation timeout, not a whole-job deadline.

Optional `stt.local.prewarm: true` loads already cached ASR weights on voice
startup without downloading. `stt.local.unload_after_idle_seconds: 0` keeps them
resident (at a memory cost). Normal transcription remains the fallback.

Optional `voice.tool_ack.cache_audio: true` reuses only explicitly configured
fixed acknowledgement phrases. Audio cache identity includes TTS configuration.
Full answers are never cached through this path. Startup warming synthesizes
without playback. The existing acknowledgement-before-inference and echo
protection ordering is retained.

Queue timing/recipient outcomes are stored in outbox.sqlite. `voice_latency`
logs mark summary, enqueue and ASR prewarm stages. There are no extra LLM calls.

## Validation / rollout

Use `scripts/run_tests.sh` for outbox, general voice, continuity, SMTP, ASR,
wake acknowledgement and streaming-TTS tests. External SMTP is mocked;
the fresh-process test exercises real plugin discovery and sender dispatch.
Human ASR/TTS latency and actual external inbox receipt remain separate checks.

Deploy only reviewed hunks to the legacy dirty laptop runtime, with per-file
backups. Do not interrupt an active CLI; restart it manually to load changed
modules. Record the exact code commit and runtime/config checksums in the
private fleet operations repository. Rollback restores those files/config;
do not replay uncertain queue jobs or delete delivery ledgers.
