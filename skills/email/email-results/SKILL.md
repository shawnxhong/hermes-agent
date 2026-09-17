---
name: email-results
description: Send this conversation's requested answer or details by email only when the user explicitly asks.
---

# Email requested results

Only send when the user explicitly asks. A long or complex answer is not
permission to send email. Reuse the relevant conversation content; when the
user asks for more detail, prepare those details without narrating them aloud.

Call `send_message` with `action=send`, `target=email` for the configured default
recipient list, and the requested content in `message`. If the user supplies a
different address, use `target=email:address` for this send instead. Do not add
the default recipients or change settings or long-term memory.

If no default recipient is configured, ask for the address in one ordinary
reply. If an address is ambiguous, ask once and use the user's confirmation
from the conversation; never repeatedly ask the same question.

Include useful detail and actual source links where available. Never invent
missing information or describe an unfinished task as completed. Do not expose
credentials, unrelated conversation content or tool logs.

Send once. Do not automatically repeat an uncertain or failed send. Say "sent"
only on a successful tool result, "queued" only if that is the actual result,
and report failures briefly. Never read the email body or addresses aloud.
