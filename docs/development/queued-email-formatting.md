# Queued email formatting

The demo `email_send(subject, body)` schema accepts `body` as an array of
Unicode Markdown blocks. An item can contain actual newlines, even an entire
prepared document. Legacy one-line items and optional empty items still work.
The handler joins blocks with LF, preserving all internal line breaks without
rewriting or guessing. Legacy string handler callers remain compatible.
The SMTP worker uses `hermes_cli.email_render.render_email` to create UTF-8
`multipart/alternative`, readable plain text first and HTML second, with
quoted-printable encoding and SMTP-policy CRLF serialization.

Presentation is deterministic: headings, emphasis, lists, links, tables, quotes,
and fenced code. Single newlines become explicit HTML breaks; blank lines remain
paragraph boundaries. Code-block whitespace and literal backslash escapes are
preserved. Raw HTML is shown as escaped text. Generated markup is allowlisted,
links support only HTTP/HTTPS/mailto, and images become alt text without fetching
external resources. Styles are simple and inline; Outlook acceptance is still
required for the specific desktop/web client in use.

A document already flattened into one line cannot be reconstructed reliably.
The original prompt-only guidance was insufficient: a real long travel plan
contained adjacent heading/list markers and zero LF characters in both native
tool arguments and the queue, despite the new renderer. Explicit line arrays
remove reliance on long JSON-string newline generation. Field guidance and both
demo skills retain explicit array boundaries, while accepting valid multiline
blocks so existing Markdown does not need line-by-line regeneration.
This cannot infer missing structure from a legacy flattened string. Do not
heuristically split sentences, decode every backslash-n, or use another LLM to
rewrite the mail. The two-field tool API, turn deduplication, recipient selection
and SMTP outcome classification remain unchanged.
A presentation failure occurs before SMTP and is recorded as message_render_failure.

All three explicit plugin variants (CLI, Gateway, services) use the same helper.
The generic upstream email platform adapter is a separate send path and is not
changed by this demo queue repair. Filesystem paths and Markdown attachments
are not accepted by this tool; pass the document contents in body.

## Verification

Run `scripts/run_tests.sh tests/hermes_cli/test_email_render.py` inside an
isolated candidate Docker container, with a temporary HERMES_HOME and no
production volumes or credentials. Tests cover the real queue Unix socket,
SQLite store and SMTP sender with a loopback-only SMTP receiver, MIME roundtrip,
Unicode, line breaks, tables, code, filtering, long lines and bounded retries. Relevant
upstream mail transport/deduplication tests should also pass. No external email
is sent by these tests.

Before activation, build pinned role images from the accepted parent images.
CLI/Gateway require reviewed migration of the persistent email-results skills;
new image defaults do not replace already materialized Markdown. Preserve queues
and turn identities and do not replay past jobs. Existing queued bodies would
use the new renderer when processed; no queued jobs were modified during this
work. No private asset update is required. See the retry migration and rollback
constraints below before changing worker versions.


## Native-model regression

`scripts/local-ovms/check_email_line_array.py` runs the real native agent against
local OVMS, dispatching the actual registered plugin (including deferred
`tool_call` dispatch), into a real isolated Unix-socket/SQLite queue. No SMTP
worker runs. It checks the model emits an array, the queue contains real LF
characters, and HTML has headings/lists/tables. It covers a travel-plan composition,
an existing Markdown document with a table/code block, a home checklist and
a follow-up request to email a plan already in the conversation. It uses the
actual deployment output budget of 1024, not the previous 2048-token test budget.
Run in Docker with a fresh temporary HERMES_HOME and no production mounts. The
script's own synthetic artifacts are suitable for inspection. This verification
complements the earlier preformatted synthetic SMTP sample; that sample alone
could not validate real model-generated input.


## Asynchronous delivery retries

The worker attempts delivery up to **three times per recipient, including the
initial attempt**. SMTP acceptance is success; it does not prove inbox placement
or reading. Every non-success result, including uncertain submission outcomes,
schedules another attempt after 10 seconds, then 30 seconds. Successful recipients
are never sent again. After the third attempt the final result remains `failed`
or `unknown`, preserving the distinction between rejection and an unconfirmed
submission. Multi-recipient jobs can end `partial`. Other recipients and newer
jobs can proceed while retries wait. The agent only awaits the durable enqueue
acknowledgement; retries do not call the agent or delay its conversation. Final
outcomes are available through the administrator's queue status command, which
now includes `attempts`, `next_attempt` (Unix time), and the latest error.

Attempt counts are committed before SMTP submission. Worker restart resumes
interrupted active deliveries with a 30-second delay and the same total attempt
limit. All attempts reuse the recipient's Message-ID. An uncertain SMTP outcome
may already have delivered the message, so retry can create duplicates even with
a stable Message-ID; exactly-once SMTP delivery is not guaranteed. No delivery
receipt or delayed bounce processing is provided.

Opening the store adds `attempts` and `next_attempt` columns to existing recipient
rows. Existing terminal jobs are never requeued. Legacy in-flight deliveries count
as one consumed attempt. This is a forward schema migration: the previous worker
uses positional INSERTs and cannot write the expanded schema. Before deployment,
back up the database while the worker is stopped. For rollback after new traffic,
retain the new-schema database and use a compatible worker or an explicit reviewed
schema migration; do not restore an old queue snapshot and risk replaying mail.


## Slow preparation and repeated send generation

`preparing email_send` is a streaming indicator raised when the model starts
producing the tool name/arguments. It is not SMTP delivery progress. Production
records showed 40–80 ms local enqueue results but repeated lengthy generation:
the model supplied a one-item array containing valid multiline Markdown, which
the old schema's no-newlines regex rejected before dispatch. It regenerated the
same content as individual lines (one observed repair took about 88 seconds).
Separately, the 1024-token voice output budget truncated long draft JSON; the
previous native test used 2048 and did not catch this deployment mismatch.

The schema and normalizer now accept multiline blocks. The skill reuses prepared
content, avoids format-repair narration and suggests a compact 150–220-word draft
for ordinary new plans; explicit long requests/documents are not truncated by
this guidance. Model output caps and stream interruptions remain real limits:
this does not claim arbitrary-length text can be generated in 1024 tokens.

Prompt instructions alone also failed a real 1024-token regression: after a
successful enqueue the model still produced a second email call. The plugin
therefore uses the generic `finish_tool_turn` policy hook, invoked only after a
tool batch has executed and persisted and guardrails have passed. A lone
`email_send` result with queued/duplicate and job ID returns the fixed queued
acknowledgement through the normal native turn finalizer, without another LLM
call. No intent router, extra agent, prompt-prefix mutation or hidden SMTP wait
is introduced. Mixed batches, other tools, errors, missing receipts and failures
to persist retain the native loop. Interrupt checks take precedence.

SMTP/retry behavior is unchanged and asynchronous. An event-gated Unix-socket
regression proves enqueue acknowledgement arrives while the SMTP sender is still
blocked. Real native-model regressions assert one email tool call, one queued job,
valid Markdown HTML and normal completion at the actual 1024-token budget.
A final installed-image 1024-token run still hit one length stop on the longer
checklist (about 93 seconds including recovery), despite one accepted tool call.
Prompt brevity alone is therefore insufficient. The candidate's versioned CLI
and Gateway profile templates raise model.max_tokens from 1024 to 2048; the
recipe declares that explicit persisted-config migration. Native checks record
every real API request's cap and finish reason, and --assert-no-truncation rejects
both length stops and implicit retry budget boosts. The default test budget stays
1024 so reproductions cannot silently hide the old limit. Raising the allowance
does not force long spoken responses; existing voice brevity guidance remains.
These changes are development-branch only; main and production baseline .1 are
not modified or deployed by this task.
