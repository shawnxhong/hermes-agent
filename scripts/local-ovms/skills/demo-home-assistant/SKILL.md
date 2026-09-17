---
name: demo-home-assistant
description: Check and control simulated home appliances.
version: 0.2.0
author: shawnxhong
platforms: [linux]
metadata:
  hermes:
    category: productivity
    tags: [home, appliances, simulation, english]
---

# Demo Home Assistant

Use English for questions about appliance state or switching home devices on or
off. This is a simulated home, not real IoT. The service is the only source of
truth; never role-play state or successful control. Do not apply this skill to
travel, coding or general knowledge.

Available IDs: `living_room_ac`, `master_bedroom_ac`, `second_bedroom_ac`,
`lights`, `living_room_tv`, and `robot_vacuum`. Only `on` and `off` are supported.

1. When the current request concerns home appliance state or control, call
   `demo_home_status` for fresh state. Never use memory as live device state.
   Do not call home tools for unrelated requests.
2. For "Did I leave anything on?", report all six devices grouped by state and
   ask whether to turn off those currently on. Start with "In the demo home".
3. Ask through the ordinary reply, not `clarify`. A direct command already
   authorizes the named devices. A following "yes" authorizes only the devices
   just proposed; a narrowed answer such as "only the lights" narrows the set.
4. Before control, query again and pass explicit IDs plus `on` or `off` to
   `demo_home_set`. Verify the returned state before reporting success.
5. For a read-only question such as "the bedroom AC", report all matching
   devices from one fresh query; do not ask which bedroom merely to report state.
   An ambiguous control target such as "turn off the AC" requires one short
   question before changing devices. If a tool
   fails, report that the result was not confirmed and do not retry, search,
   edit the database or invent replacement state.

Do not invent temperatures, power use, schedules or cleaning progress. Keep
voice responses within three short sentences and 100 words. IM may show a table.
Do not email unless explicitly requested. Leave unrelated turns to normal Hermes.
