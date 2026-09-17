# Local asynchronous email queue

Model interface: `email_send(subject, body)` only. Native CLI/IM turn context
provides the idempotency scope; missing/closed context is rejected. The service
snapshots configured default recipients and commits to private SQLite before
acknowledging. No destination/action/status parameters are model-controlled.

The plugin registers only a tool, no global prompts, routing or lifecycle hooks.
The system service runs as the local user, independently of CLI and Gateway,
behind a 0600 Unix socket. No SMTP worker is started inside the agent. Disable
the native voice_delivery toolset and enable email_queue on each supported
platform; keep tool_search off. The resulting eight-group profile has fifteen
direct tools. Gateway platform replies do not depend on the removed model tool.

## Delivery and recovery contract

- One committed job per `(profile, session, turn)`, including concurrent calls
  with rewritten titles/bodies. First valid payload wins, even if delivery fails.
- A new user turn can request a new send. This is not permanent content dedup.
- `queued` means accepted locally, NOT delivered. `duplicate` references the
  original job and status. An IPC timeout after submission is `unconfirmed`:
  do not retry or assume failure.
- Each recipient is submitted at most once. SMTP accepted means `sent`, not
  verified inbox delivery. Definite failures, uncertain outcomes and per-address
  partial results are retained. Connection/auth failure skips remaining addresses.
- No automatic delivery retry. Existing adapter IPv6-to-IPv4 connection fallback
  is preserved before SMTP submission; accepted or uncertain DATA is never replayed.
- Restart resumes untouched queued jobs; interrupted sending jobs become unknown,
  with untouched recipients skipped. CLI exit/scene switching does not cancel a
  durable job. No old voice-outbox jobs are migrated or replayed.
- SMTP uses the established adapter configuration/TLS connection helper without
  starting IMAP. New messages use the requested subject, not the reply helper's
  `Re:` prefix. No attachments/custom recipients in v1.
- Database stores private message bodies and per-recipient results. Logs/status
  avoid bodies and credentials. Same-user filesystem access remains an operating
  system trust boundary; this is not a hostile-process SMTP firewall.

Read-only operator command: run the deployed `email-queue/mail_queue.py status
--home /home/agentdemo/.hermes` using the Hermes Python environment. There is no
model status tool or automatic resend command. Old in-memory CLI agents must
be restarted to remove their cached send_message surface.

## Verification

Run `scripts/run_tests.sh -j 4 tests/cli/test_email_queue_plugin.py
tests/cli/test_native_voice.py tests/cli/test_voice_outbox.py
tests/cli/test_voice_sentence_delivery.py`.

`scripts/local-ovms/check_email_queue.py` uses real local OVMS, current curated
skills/toolsets and native voice prefix (CLI), but fake search and a private
queue without any SMTP worker/audio/control actions. Cases: direct, travel,
mixed; platforms: cli, feishu, weixin. Record tool count separately from durable
job count. `--strict-single-call` makes model duplicate calls a separate failure.

Initial real-model checks: direct CLI one call/one job; travel then email one
call/one job. Feishu mixed flow called twice, but got queued then duplicate with
one job; subsequent unrelated arithmetic answered normally. This is NOT proof
of universal model compliance: replies may still be verbose or promise timing.
Weixin direct request also queued once using the real native turn context.
The four focused regression files passed 66 tests. No real inbox delivery or
microphone/speaker acceptance is claimed.

Rollback: stop and disable the system service BEFORE restoring the backed-up
config and plugin/skill pointers. Preserve the queue database, never replay it
implicitly. Baseline `baseline/laptop-20260917-eight-tools` stays unchanged.
