---
name: email-results
description: Queue requested details for the configured default email recipients.
---

# Email requested details

This skill is already loaded; do not list skills or read it again.
When the user asks for email, reuse the relevant conversation and prepare the
requested details. Do not narrate the preparation or read the body aloud.

Call `email_send` once, with only these two fields:
```json
{"subject":"London travel guide","body":"The requested details and sources."}
```
Use a short, single-line subject and a nonempty body. Replace both example
values with the user's requested content. Recipients are configured by the
service; never ask for an address or use another messaging tool.

After `queued`, say briefly: "I've queued the details for email."
After `duplicate`, no new email was added; do not submit again.
After `rejected` or `unconfirmed`, report the result briefly; do not retry.
Do not wait for delivery, poll status, claim the email arrived, or promise when it will arrive.

Only send when requested, including a details request authorized by the active
travel or flight skill. Do not include unrelated content, secrets or tool logs.
This tool only supports default recipients; explain that limitation if the
user explicitly requests a different address, and do not send to defaults instead.
