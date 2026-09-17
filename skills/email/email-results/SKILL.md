---
name: email-results
description: Send this conversation's requested answer or details by email only when the user explicitly asks.
---

# Email requested results

Only send when the user explicitly asks. A long or complex answer is not
permission to send email. Reuse the relevant conversation content; when the
user asks for more detail, prepare those details without narrating them aloud.

The tool name is `send_message`; the default delivery target is `email`.
They are different fields. Never put `send_message` in `target`.

Call `send_message` directly with this parameter shape:
```json
{"action":"send","target":"email","message":"The requested details go here."}
```
If the tool is unavailable, explain briefly instead of using another tool.
If a call is rejected for invalid parameters before
delivery, correct the parameters once using the example; if it still fails,
stop and explain briefly. An uncertain delivery outcome is not permission to retry.

`target=email` uses the configured default recipient list; no address question
or target-list call is needed. For a different user-supplied address, set
`target` to `email:address` for this send only. Do not add the default recipients
or change settings or long-term memory.

If no default recipient is configured, ask for the address in one ordinary
reply. If an address is ambiguous, ask once and use the user's confirmation
from the conversation; never repeatedly ask the same question.

Include useful detail and actual source links where available. Never invent
missing information or describe an unfinished task as completed. Do not expose
credentials, unrelated conversation content or tool logs.

Send once. Do not automatically repeat an uncertain or failed send. Say "sent"
only on a successful tool result, "queued" only if that is the actual result,
and report failures briefly. Never read the email body or addresses aloud.
