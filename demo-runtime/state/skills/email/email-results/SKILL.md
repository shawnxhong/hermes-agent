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
Use unnumbered section headings and Markdown bullet lists (-) for newly composed
mail. Use bold labels inside bullets; at most one nested bullet level. Do not use
1., 2., 3. to number ordinary sections or options. Keep real numbers such as days,
prices and times. Put blank lines before headings and lists. Keep emphasis markers
paired around text; never put ** on a line by itself. Do not wrap entire sections
in bold. Do not shorten or rewrite a supplied document just to satisfy this default.

Call `email_send` ONCE with subject and body, an array of Markdown text blocks:
```json
{"subject":"Travel plan","body":["# Travel plan","## Transport\n- Train option\n- Flight option","## Itinerary\n| Day | Plan |\n| --- | --- |\n| 1 | Visit |"]}
```
Real newlines inside an item are valid. A whole already prepared Markdown document
can be one array item; do not split and regenerate it line by line. Separate
items create newlines; empty items are optional blank lines. No HTML or double
escaping. Recipients are configured by the service; never ask for an address.

`queued` or `duplicate` means the request is already accepted: STOP calling tools
and give one final short acknowledgement, "Your email has been queued for sending."
Never rewrite the body or submit another call after acceptance. On `rejected` or
`unconfirmed`, state the result briefly and stop; do not retry or repair in a loop.
The SMTP worker sends and retries in the background. Do not wait for delivery,
poll status or claim arrival. Only send when the user or active travel/flight
skill authorizes it. Do not include unrelated content, secrets or tool logs.
Alternate recipients are unsupported; explain instead of sending to defaults.
