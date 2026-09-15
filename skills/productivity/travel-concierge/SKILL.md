---
name: travel-concierge
description: Turn a destination idea into a brief intake question and a tabular itinerary.
version: 0.3.0
author: shawnxhong, with Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: productivity
    tags: [travel, itinerary, voice, email]
---

# Travel Concierge

Use English. Apply this skill only to trip, itinerary or destination-planning
requests. A place mentioned in an unrelated question does not activate it. A new
destination starts a new trip and never inherits old trip facts. Do not book or
claim any external transaction.

## Required flow

The only routinely required facts are destination, duration and departure city.
Use any of them already supplied. Dates, budget, lodging, interests and traveler
profile are optional unless the requested result depends on them.

1. If duration or departure city is missing, name two or three defining sights or
   experiences, then ask only for the missing facts. Use no more than two sentences
   and 45 words, end with `?`, and stop. Ask in the ordinary reply, not `clarify`.
2. Once all three required facts are known, immediately produce the complete plan.
   Do not repeat the intake question or ask about budget, month or hotel style.
3. If the first request contains all three facts, skip directly to the plan.

Example first reply: "Melbourne is known for its laneways, coffee culture, and
the Yarra waterfront. How many days will you have, and which city will you travel
from?"

## Complete plan

Use stable knowledge for an ordinary, season-neutral itinerary. Search only when
the user asks for current prices, schedules, opening status, availability or other
date-specific facts. Make one focused search, preferably using an official source;
a second is allowed only for one unresolved critical fact. After a failed search,
do not retry or switch browsing methods. Continue conservatively and label what
was not verified.

Include:

- A title and one-line facts or assumptions summary.
- Practical outbound/return transport, arrival transfer and local transport.
- One Markdown table with exactly these columns:

  `Day | Area or theme | Morning | Afternoon | Evening and logistics`

- Important reservations and date-specific uncertainties.

For 1–14 days, write one row per day; for longer trips, group sensible ranges.
Keep each day geographically coherent with realistic transit, meals and rest. Do
not duplicate attractions to fill rows. The header, separator and every row must
be on separate physical lines and must not be inside a code fence. Do not invent
direct routes, exact fares, timetables, availability, links or reservations.

## Delivery

For buffered local voice, return the complete plan. The voice host emails that
full result to the configured or user-confirmed recipient and separately speaks a
short summary. Do not call `send_message`, speech or TTS, and do not claim email
success yourself.

For IM or typed chat, show the complete plan directly. Email only when explicitly
requested, using the native message tool and its normal delivery result.

Do not store trip facts or recipients in long-term memory. Leave unrelated tasks
to normal Hermes behavior.
