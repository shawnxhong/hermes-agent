# Queued email formatting

The demo `email_send(subject, body)` model schema accepts `body` as an array of
Unicode Markdown lines. Each heading, paragraph, list item, table row or code
line is a separate item; empty items create blank lines. The handler joins these
with real LF characters before submission. Legacy string callers are accepted by
the handler for compatibility. The queue retains the resulting string unchanged. The SMTP worker uses
`hermes_cli.email_render.render_email` to create a UTF-8 `multipart/alternative`
message with readable plain text first and HTML second. Both parts use
quoted-printable; Python's SMTP policy serializes CRLF and MIME headers.

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
demo skills show the line-array contract with blank lines and structured examples.
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
an existing Markdown document with a table/code block, and a home checklist.
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
