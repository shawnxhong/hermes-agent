---
name: demo-home-assistant
description: Check whether home appliances are on, or turn selected simulated appliances on or off.
---

# Home appliances

Use the existing `demo_home_status` and `demo_home_set` tools. This is a
simulated home, not real IoT; never invent states or successful changes.

For a status question, query once and report the relevant devices briefly.
If asked about all appliances, group them into on and off, then ask whether to
turn off those that are on. A read-only bedroom question can report both
bedrooms without asking which one.

For control, use a fresh status result and pass the exact selected device IDs
and `on` or `off` to `demo_home_set`. A direct, unambiguous command authorizes
those devices. If the target is ambiguous, ask one ordinary short question.
"Yes" confirms only the action just proposed in this conversation.

Confirm changes only from the returned state. If a tool fails, say the action
was not confirmed and stop; do not repeatedly query, use a browser or edit the
simulator database. Finish with 1–3 short English sentences.

Do not email automatically, store device state in memory, or use these tools
for unrelated tasks.
