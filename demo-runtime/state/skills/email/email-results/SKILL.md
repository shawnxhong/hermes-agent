---
name: email-results
description: Queue requested details for the configured default email recipients.
---

# Email requested details

This skill is already loaded; do not list skills or read it again.
When the user asks for email, reuse the relevant conversation and prepare the
requested details. Do not narrate the preparation or read the body aloud.

Call `email_send` once, with only these two fields. `body` is an array of
Markdown lines, NOT one long string:
```json
{"subject":"London travel guide","body":["# London travel guide","","## Transport","- First option","- Second option","","## Notes","A short paragraph."]}
```
Use a short, single-line subject. Replace the example with the requested content.
Each heading, paragraph, list item, table row and code line must be its own array
item. Insert an empty string between paragraphs or sections. Do not concatenate
headings and lists into one item. The service inserts real newlines and renders
HTML plus readable plain text; do not supply HTML or escaped newline sequences.
For an existing Markdown document, read it and split its contents at actual line
breaks into the array, preserving empty lines, indentation and code fences.
Recipients are configured by the
service; never ask for an address or use another messaging tool.

After `queued`, say briefly: "I've queued the details for email."
After `duplicate`, no new email was added; do not submit again.
After `rejected` or `unconfirmed`, report the result briefly; do not retry.
Do not wait for delivery, poll status, claim the email arrived, or promise when it will arrive.

Only send when requested, including a details request authorized by the active
travel or flight skill. Do not include unrelated content, secrets or tool logs.
This tool only supports default recipients; explain that limitation if the
user explicitly requests a different address, and do not send to defaults instead.
