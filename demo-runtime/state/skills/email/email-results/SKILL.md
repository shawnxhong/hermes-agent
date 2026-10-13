---
name: email-results
description: Requested delivery using the sole configured channel.
---

# Send requested details

This skill is already loaded; do not load it again.
The delivery tool describes the only active channel. Users cannot change it
or recipients. If a different channel or recipient is required, explain the
limitation and do not silently send elsewhere. Never edit configuration.

Prepare useful requested details and sources, not just a heading. Call
`deliver_details` once with `title` and `content`. No other parameters.
After `queued`, briefly name the returned channel and say queued, not delivered.
For duplicate, rejected or unconfirmed, report accurately without retrying.
Do not poll, read the content aloud, include secrets or promise arrival.
In IM, reply directly unless external delivery is requested.
