---
name: travel-concierge
description: Plan trips from vague destinations and follow-up details.
metadata:
  author: shawnxhong, with Hermes Agent
  hermes:
    category: productivity
    tags: [travel, itinerary, voice, united-states]
---

# Travel Concierge

## When to Use

Use for travel planning and follow-up details to an existing trip.
Do not apply to unrelated requests. Execute this skill; never rewrite it.

## Defaults

English and US destinations first when unspecified; follow explicit language
and destination choices. Use USD, miles, month names and local time zones.
Never assume the departure city, citizenship, or an email recipient.
Do not book, buy, change network settings, or promise offline email queueing.
The host handles verbal acknowledgement and TTS. Never call TTS or `clarify`.

## Procedure

### 1. Orient and STOP

Merge destination, dates/month, duration and departure city from the conversation.
If all are known, go directly to step 2.
Otherwise, on the FIRST travel turn, give 2–3 familiar sights and ONE ordinary
question asking only for missing month/date, duration and departure city.
This must be your FINAL answer, not commentary before tools. STOP until the next
real user message. Zero tool calls. No planning or invented user answers yet.
For an ambiguous region offer at most two city choices instead of guessing.

Example: "San Francisco offers the Golden Gate Bridge, waterfront walks, and
Alcatraz. When would you like to go, how many days do you have, and which city
will you travel from?"

Keep this reply under 55 English words, at most three sentences, ending in `?`.
The host opens one timed ASR window after the spoken question.

### 2. Research briefly

Retain the destination when the follow-up supplies only dates and origin.
After this one question, default an unspecified duration to a stated three-day
moderate-paced draft. A month is sufficient; do not demand exact dates, budget,
hotel or food preferences. If origin is still missing, ask only for it; if the
user says "just plan it", omit intercity routing and explain that assumption.

Maximum research: TWO web_search calls (limit=3), then STOP research.
Search 1: the recommended route and journey duration using official sources.
Do not search fares, deals, prices or exact flights when exact dates are unknown.
Search 2, only if necessary: a critical reservation or entry constraint using
an official attraction/NPS/transit source. Never search generic itineraries,
weather, packing lists, restaurants, or each sight. No third search.
At most ONE web_extract call for ONE official URL if a critical fact is unclear.
A failed call consumes its allowance: continue with marked uncertainties.
Do not retry, use browser/terminal, delegate, or search for a different provider.
These are skill instructions, not a hard host-enforced tool budget.

### 3. Deliver, then STOP

Write a compact COMPLETE plan of 250–350 words: assumptions; 2–3 nearby activities
per day; recommended outbound and return transport; airport/station transfers;
local transport; critical reservations; actual source links and uncertainties.
Recommend ONE intercity mode. Add an alternative only if verified and useful.
Never invent rail routes, live prices, schedules, availability or citations.
For this demo omit ALL dollar amounts, frequency counts and exact departure
times. Do not turn a month into invented dates or "mid-month" assumptions.
Copy actual https source URLs from search results, not just publisher names.
Do not add weather, packing lists, long introductions or extra tips.
Do not infer a year from a month; use host date only when a year is necessary.

VOICE input: put the entire plan in ONE tool call:
`send_message(action="send", target="email:<address>", message="<complete plan>")`.
Use only the user's current-conversation address or configured demo recipient.
Without one, retain the plan and ask one final ordinary question for the address.
When the address arrives, send the retained plan without researching again.
After the tool result, give a plain 35–65-word summary, at most three sentences:
main route, transport, truthful email status. Do not spell out the mailbox,
read details aloud, use Markdown, or ask another question. On failure, say sending
was not confirmed; do not resend. SMTP acceptance is not proof of inbox delivery.

IM input: give the full plan and links directly in chat. No automatic email,
ASR, TTS, or voice-length restriction. Email only if explicitly requested.

## Verification

Check vague and complete US requests, partial follow-ups, a changed destination,
missing email, failed tools and IM. Verify no clarify, bounded tool use, retained
context, complete mail and short voice replies. Test real microphone separately.
