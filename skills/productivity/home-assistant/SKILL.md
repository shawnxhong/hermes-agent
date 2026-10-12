---
name: home-assistant
description: Check and control simulated home appliances.
---

For appliance questions only, call `demo_home_status` once for fresh state.
Report requested devices briefly; a bedroom query may include both bedrooms.
For all-device queries, group on/off devices and offer to turn off those on.

A clear control request authorizes only its targets. Use fresh device IDs with
`demo_home_set` and `on` or `off`. Ask one short question for an ambiguous
target; "yes" confirms only the action just proposed.

Confirm changes from returned state only. On failure, report that the action
is unconfirmed and stop. Never invent state, edit the database, or send email.
This is a simulator, not real IoT.
