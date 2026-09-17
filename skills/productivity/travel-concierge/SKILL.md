---
name: travel-concierge
description: Give a short destination overview or practical trip itinerary using English Wikivoyage.
---

# Travel planning

Use this skill for destination advice or an itinerary, not unrelated questions.
Answer in English and keep the spoken answer to 1–3 natural sentences.

Use the destination and trip length already provided. If either is missing,
give a brief overview and ask only for the missing information in an ordinary
reply. Ask for the departure city only when transport advice requires it.
Do not routinely ask about budget, hotels or interests.

Make one focused `web_search` restricted to `site:en.wikivoyage.org` for the
destination. If necessary, read one matching page using `web_extract`. Use only
this site. If access fails, do not retry, switch sites or open a browser; explain
briefly what was not verified and offer clearly labelled general suggestions.

Suggest a compact route with a few highlights and practical local transport.
For a longer trip, group days instead of reading a long day-by-day table.
Never invent live prices, schedules, direct flight routes or reservations.

Do not email automatically. If the user explicitly asks for email, load
`email-results` and send the requested plan there; keep the spoken reply short.
Use conversation history for follow-ups, not long-term memory. Answer a new,
unrelated task normally without applying this travel procedure.
