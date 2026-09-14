---
name: demo-home-assistant
description: Check and control simulated home appliances.
version: 0.1.0
author: shawnxhong
platforms: [linux]
metadata:
  hermes:
    category: productivity
    tags: [home, appliances, simulation, bilingual]
---

# Demo Home Assistant Skill

Check and control the local simulated home, not real appliances.
The service owns persistent state; you must not role-play successful actions.

## When to Use

Use for questions about appliances at home, whether anything was left on,
and requests to turn household devices on or off, in English or Chinese.
Do not apply this workflow to travel, coding, general knowledge or other tasks.

## Prerequisites

The enabled native `demo-home` plugin and local simulator service provide
`demo_home_status` and `demo_home_set`. If necessary discover the demo_home
toolset with native tool discovery. No Internet or real IoT access is needed.

## How to Run

Query first; report actual results. Control only what the user authorized.
Use the returned post-operation snapshot to verify success.

## Quick Reference

| Device ID | English aliases | 中文 |
|---|---|---|
| living_room_ac | living room AC, living room air conditioner | 客厅空调 |
| master_bedroom_ac | main/master/primary bedroom AC | 主卧空调 |
| second_bedroom_ac | second/guest bedroom AC | 次卧空调 |
| lights | lights, the light (one collective device) | 电灯、灯 |
| living_room_tv | living room TV, television, TV | 客厅电视、电视 |
| robot_vacuum | robot vacuum, vacuum cleaner, cleaning robot | 扫地机器人 |

Only on/off is supported. Do not invent temperatures, rooms, schedules,
power readings or cleaning progress. Ask one short spoken question if an
ambiguous device cannot be resolved (for example, "the AC" with three ACs).

## Procedure

1. Call `demo_home_status` for current state. Never reuse an earlier turn's
   state as if it were fresh, and never store device states in memory.
2. For "Did I leave anything on?", report **all six devices**, grouped by
   on/off, followed by one brief question asking whether to turn off the
   devices currently on. If all are off, say so and do not ask needlessly.
3. In the first appliance response, naturally say "In the demo home" or
   "模拟家庭里" so the simulation is clear; do not repeat a long disclaimer.
4. Ask via ordinary final reply, not the `clarify` tool. The existing voice
   interface handles the tone and follow-up recording. Do not call TTS yourself.
5. A direct command, such as "Turn off the TV", already authorizes that
   operation: query, then call `demo_home_set` without another confirmation.
6. "Yes" immediately answering your shutdown question authorizes only the
   proposed devices. Re-query and set those device IDs to `off`. "Only the
   lights" narrows the selection. "No" or cancellation changes nothing.
   Never interpret a yes after a topic change as appliance authorization;
   ask what the user means if there is no current unambiguous proposal.
7. `demo_home_set` takes explicit IDs and a desired `on` or `off`, not a
   toggle. It returns the committed full snapshot and changed IDs. Verify
   each requested device matches before saying it is off/on. If already in
   the requested state, say so rather than claiming a new change.
8. On failure, briefly state that status/action could not be confirmed.
   Do not pretend success, repeatedly retry, edit the database, reset the
   demo, search the web, or generate replacement mock data.

## Pitfalls

- Match the user's language; English is the default. In voice, stay within
  three short sentences and 100 English words / 240 Chinese characters.
  Grouped status can cover all devices without reading IDs or JSON aloud.
- Example structure only, **not assumed state**: "In the demo home, the
  living room and second bedroom ACs, lights, and TV are on. The main bedroom
  AC and robot vacuum are off. Would you like me to turn off what's on?"
- In IM, a complete status list/table is fine; do not activate local audio.
- This is a simple status/action task: no detailed report or automatic email.
  Use email only if the user explicitly requests it, following normal delivery.
- The operator reset command is maintenance, not a model tool or user workflow.

## Verification

Only service results establish state or completed actions. Check the returned
`success`, `simulated`, target IDs and states; preserve uncertainty on errors.
Leave unrelated conversations to native Hermes behavior.
