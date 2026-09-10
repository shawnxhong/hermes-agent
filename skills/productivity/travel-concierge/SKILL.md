---
name: travel-concierge
description: Stage trip advice into a question and tabular itinerary.
version: 0.2.0
author: shawnxhong, with Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: productivity
    tags: [travel, itinerary, voice, email]
---

# Travel Concierge

Turn a destination idea into a practical itinerary through a predictable
two-stage exchange. First orient the user and collect only the essential trip
scope; then produce the complete plan as a table. Do not book, reserve, purchase,
or claim completion of any external transaction.

## When to Use

Use for requests to plan a trip, build an itinerary, or give destination and
transport advice, plus follow-ups that supply details for that trip. Do not use
merely because an unrelated question mentions a place, restaurant, flight, or
past travel. A clearly new destination-planning request starts a new trip and
must not inherit facts from an earlier one. Execute this skill; never rewrite it.

### Required state transition

This transition is mandatory for every matched request, including requests phrased
only as destination "advice," "suggestions," or "recommendations":

- Missing duration or departure city makes those facts essential. The first final
  reply MUST give the brief overview, ask for the missing fact or facts, end in
  `?`, and stop. An overview alone is not a completed response.
- Once destination, duration, and departure city are known, the next final reply
  MUST contain the completed itinerary table in that same response. Do not ask
  another routine question and do not output JSON, extracted fields, a plan for
  later work, or any other intermediate representation.
- For an ordinary season-neutral itinerary, prefer stable knowledge and avoid
  retrieval to keep latency low. Retrieval is most useful when the user explicitly
  requests current or date-specific facts.

## Prerequisites

Prefer English and US destinations only when the user leaves language or region
unspecified; always honor an explicit language and destination. Never infer the
departure city. Treat trip duration and departure city as the only routine facts
required before planning. Exact dates, budget, lodging class, food preferences,
and traveler profile are optional unless the user's requested result depends on
them.

For routine trip details, ask through the ordinary final reply rather than the
`clarify` tool. Do not call speech or TTS tools: the host owns playback. Do not
store trip facts, recipients, or delivery preferences in long-term memory.

## How to Run

Merge the destination, duration, departure city, dates, interests, mobility
needs, and other constraints already supplied in the current trip conversation.
Interpret common durations such as "a weekend" or "about a week" normally.
Apply corrections to the current trip without reversing origin and destination.

Choose the stage from the facts, not from the number of messages:

1. If either duration or departure city is missing, give the brief destination
   orientation and ask only for the missing item or items.
2. Once destination, duration, and departure city are known, generate the full
   tabular plan. Do not repeat the question or add another routine question.
3. If all three facts arrive in the first request, skip directly to the plan.

## Quick Reference

| Situation | Required behavior |
|---|---|
| New request missing duration or origin | Brief overview, one ordinary question, then stop |
| User supplies the missing facts | Full tabular itinerary using all supplied facts |
| Complete first request | Full tabular itinerary immediately; no redundant question |
| Buffered local voice | Return full detail; the host emails it and speaks a short summary |
| IM or ordinary typed chat | Show the full table in chat; email only when explicitly requested |

## Procedure

### 1. Orient and ask once

Name two or three familiar landmarks, neighborhoods, or defining experiences.
Do not search or state live opening hours, prices, schedules, or availability.
Then ask only for the missing duration and departure city in one ordinary final
question. Use no more than two sentences and 45 English words, end with `?`, and
wait for the user's next real message.

Example: "Melbourne is known for its laneways, coffee culture, and the Yarra
waterfront. How many days will you have, and which city will you travel from?"

If the user already gave one fact, ask only for the other. Do not ask for dates,
budget, accommodation style, food preferences, or party size in this stage. If
the place name is genuinely ambiguous, briefly name at most two interpretations
inside the same question rather than starting a separate questionnaire.

### 2. Build the plan

After duration and departure city are known, incorporate that answer and produce
the plan without asking again. If exact dates are absent, make a season-neutral
plan and state that date-specific schedules and availability were not checked.
For an ordinary itinerary request without a request for live facts, prefer not to
search; use conservative stable knowledge and keep latency low. If the user explicitly
asks for current prices, exact schedules, present opening status, availability,
or date-specific transport, make one focused `web_search` when available and
prefer official destination or transport sources. A second search is allowed only
when one critical requested fact remains unresolved.

If a search fails or reports that search is unavailable, do not retry, guess URLs,
or switch browsing methods. Finish from conservative stable knowledge, omit any
named place or service whose current status is uncertain, and clearly label what
could not be verified.

For trips of 1–14 days, include one row for every day. For longer trips, group
logical day ranges unless the user explicitly requests a daily schedule. Keep
each day geographically coherent, normally with two or three activities, and
allow realistic time for arrival, departure, transit, meals, and rest. Avoid
duplicating attractions merely to fill rows.

The detailed result must contain:

1. A title and a one-line facts/assumptions summary.
2. A compact transport section covering the outbound and return route, arrival
   transfer, and the practical local transport approach.
3. One Markdown itinerary table with exactly these columns:

   `Day | Area or theme | Morning | Afternoon | Evening and logistics`

4. A short booking and uncertainty section for genuinely important reservations
   or facts that require date-specific verification.

Use plain, non-minified Markdown and do not wrap the response or table in a code
fence. Put the exact header shown above on its own physical line. Put the
five-column Markdown separator on the next line, then put each numbered day on a
separate line beginning with `| 1 |`, `| 2 |`, and so on. The header,
separator, and every row must each end with a newline. A sequence such as
`| Day | ... | |---| ...` on one physical line is invalid: reformat it before
returning the final response. Keep prose outside the table concise. If unsure
whether a named place, carrier, or transport service belongs to the route or
destination, omit that name or verify it first.

Do not invent direct routes, exact fares, timetables, availability, source links,
or reservation requirements. Label approximate journey times as estimates unless
they were verified from current sources.

### 3. Deliver for the active surface

For a buffered local voice turn, return the complete title, transport section,
itinerary table, and notes as the final response. Do not call `send_message`,
speech, or TTS; do not claim the email was sent. The general voice host retains
the complete response, sends it to the configured or user-confirmed recipient,
and independently produces a short spoken summary plus truthful delivery status.

For IM or ordinary typed chat, return the same complete table directly in the
conversation. Do not automatically email or play local audio on those surfaces.
If the user explicitly requests an email there, use the available native message
tool and its normal permission and delivery-result rules.

## Pitfalls

- Asking for month, budget, or hotel style after duration and origin are known.
- Repeating the first-stage question after the user has answered it.
- Returning a prose-only list or an inline, non-rendering Markdown table.
- Wrapping the final table or entire answer in a Markdown code fence.
- Returning JSON facts or an internal planning intermediate instead of the result.
- Shortening the buffered voice result itself and leaving nothing to email.
- Calling the mail tool in buffered voice instead of letting the host deliver.
- Reusing an old trip when the user begins a new destination-planning task.
- Treating SMTP acceptance as proof that an email reached the inbox.

## Verification

- [ ] A vague request gets a tool-free overview and asks only for missing
      duration/origin in at most two sentences.
- [ ] The next answer produces transport guidance and a renderable itinerary
      table without a repeated question, even when no date was supplied.
- [ ] A complete first request skips the question and produces the table.
- [ ] Every requested day is covered once and origin/destination direction is
      correct.
- [ ] An ordinary itinerary avoids unnecessary retrieval; an explicitly
      current-information request is focused and does not retry after an
      unavailable result.
- [ ] Buffered voice makes no model-owned email/TTS call; the host reports the
      captured email result and speaks only its summary.
- [ ] IM shows the full table and does not auto-email.
- [ ] A different destination starts cleanly without old trip facts.
