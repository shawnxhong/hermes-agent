---
name: travel-concierge
description: Plan trips from vague destinations and follow-up details.
metadata:
  author: shawnxhong, with Hermes Agent
  hermes:
    category: productivity
    tags: [travel, itinerary, voice, united-states]
---

# Travel Concierge Skill

Turn a travel idea into a practical itinerary and transportation advice.
Give a brief orientation, collect the missing essentials once, then deliver a
complete plan. Do not book tickets, reserve hotels, or pay for anything.

## When to Use

Use for travel planning and follow-up details to an existing trip.
Do not apply to unrelated requests. Execute this skill; never rewrite it.

## Prerequisites

Prefer English and US destinations when unspecified; honor explicit language
and destination choices. Never infer departure city or citizenship.
The local voice demo enables the `travel-voice` native workflow plugin.
It owns fact collection, bounded research, structured generation and email
submission. The host also owns acknowledgement, ASR follow-up and TTS.
Do not call TTS yourself or call `clarify` for routine travel details.

## How to Run

Merge destination, departure city, travel month/date and duration from the user
and saved trip facts. Resolve "next month" using the host's current date,
including year rollover; do not treat a relative month as missing information.
Keep the destination when the next message only provides dates and origin.
Update corrected facts without silently reversing origin and destination.

## Quick Reference

- First vague request: 2–3 familiar sights, one ordinary final question, STOP.
- Complete request: skip the introductory question and plan immediately.
- Voice: the plugin submits details, then returns only a short spoken summary.
- IM/typed input: full plan in chat; no automatic mail or local audio.

## Procedure

### First reply

Name 2–3 familiar sights without search or claims about current opening hours.
Ask ONLY for missing travel month/date, duration and departure city in one
ordinary FINAL reply ending in `?`. Wait for the next real user message.
Use at most three sentences and 55 English words. Do not ask about budget,
hotel class or food unless essential to an explicit request.
For an ambiguous region, offer at most two city choices instead of guessing.

### Planning

A month is sufficient. After one question, default an unspecified duration to
an explicitly stated three-day moderate-paced draft. Preserve all volunteered
constraints. The local voice demo currently supports 1–14 days.
Group 2–3 nearby activities per day, cover every requested day, avoid duplicate
activities, and allow time for arrival, departure, transport and rest.
Give one practical outbound/return mode, airport/station transfers, local
transportation, critical reservations and uncertainties. Do not invent rail
connections, exact schedules, fares, availability or source links.

### Voice delivery: program-owned

The plugin performs at most TWO native searches, each with three results;
a failed search stops further searching in that plan. There is no browser,
terminal, extraction or delegation fallback. The generic model tool loop is
not used for an intercepted voice travel turn.
It requests `spoken_summary` and `detailed_plan` as separate structured fields.
The complete response must finish and pass validation before any email is sent.
The detail-generation budget is separate from the spoken-word budget.
At most three local model requests are allowed per turn, including repairs.

The host uses `travel_voice.default_recipient`, unless the current user
explicitly supplies another valid address. A request not to email is respected.
Without a recipient, ask an ordinary final question and retain the plan; an
email-address answer reuses it without fresh research.
The existing email adapter sends the detail. A durable submission record
prevents automatic duplicate sends, including uncertain previous attempts.
Only the short summary (at most 60 English words/two sentences) and a truthful
host-generated delivery status reach the final display and TTS. SMTP acceptance
is not proof of inbox receipt. Do not print or speak the email body, internal
JSON, tool-call sketches, incomplete fragments or recovery continuations.

### IM and ordinary text

Keep the normal Hermes text workflow. Give the full plan directly in chat,
with useful formatting, sources and uncertainties; no automatic email or TTS.
For research, prefer official transport/attraction sources and at most two
`web_search` calls with `limit=3`. These IM limits are skill guidance, not the
voice plugin's hard budget. Email only when explicitly requested and available.

## Pitfalls

Do not change network settings, claim unverified bookings, promise offline
email queueing, invent a recipient or claim delivery without a successful result.
Do not require exact dates merely to produce a useful seasonal draft.

## Verification

Replay vague and complete US requests, partial follow-ups, changed destinations,
missing/overridden recipients, failed search/mail and typed/IM isolation.
Check complete day coverage, route direction, relative month resolution, tool
counts, concise speech and truthful delivery. Test actual microphone separately.
