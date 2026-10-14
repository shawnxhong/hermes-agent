---
name: email-results
description: Queue requested details for the configured default email recipients.
---

# Email requested details

This skill is already loaded; do not list skills or read it again.
Reuse the relevant facts or prepared plan from the conversation. Do not narrate
preparation, format repair or delivery. Do not read the email body aloud.
For a newly composed plan, use a useful compact draft (about 150-220 words unless
the user explicitly asks for more). Include the requested transport, itinerary
and practical notes; avoid introductions, repeated facts and unnecessary detail.
Do not shorten or rewrite a supplied document just to satisfy this default.

Call `email_send` ONCE with subject and body, an array of Markdown text blocks:
```json
{"subject":"Travel plan","body":["# Travel plan","## Transport\n- Train option\n- Flight option","## Itinerary\n| Day | Plan |\n| --- | --- |\n| 1 | Visit |"]}
```
Real newlines inside an item are valid. A whole already prepared Markdown document
can be one array item; do not split and regenerate it line by line. Separate
items create newlines; empty items are optional blank lines. No HTML or double
escaping. Recipients are configured by the service; never ask for an address.

`queued` or `duplicate` means the request is already accepted: STOP calling tools
and give one final short acknowledgement, "I've queued the details for email."
Never rewrite the body or submit another call after acceptance. On `rejected` or
`unconfirmed`, state the result briefly and stop; do not retry or repair in a loop.
The SMTP worker sends and retries in the background. Do not wait for delivery,
poll status or claim arrival. Only send when the user explicitly asks for email.
Requests for details or full plans are answered in this chat and do not authorize
email, including travel and flight requests. Do not include unrelated content,
secrets or tool logs.
Alternate recipients are unsupported; explain instead of sending to defaults.
