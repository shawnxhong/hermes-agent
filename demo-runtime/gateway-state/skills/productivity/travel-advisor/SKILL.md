---
name: travel-advisor
description: Destination highlights and requested travel plans in this chat.
---

# Travel highlights

This is the complete skill body, already loaded. Do not call `skill_view`
again for this skill or its `SKILL.md`. Reuse it on follow-up turns.
While tools are needed, call them without narrating plans or giving a draft
answer. Finish the necessary lookup before one final reply.
Do not answer and call another tool in the same response. If essential facts
are missing, ask one short question instead, then wait for the user's reply.

Reply directly in English. Default to 2–3 destination highlights,
not a daily itinerary. Reuse conversation facts; do not ask for dates, duration,
budget or departure city just to describe highlights. If the destination is
missing, ask one ordinary short question.

Use one `web_search` restricted to `site:en.wikivoyage.org`; extract at most one
matching page if needed. On failure, stop searching and label general advice
as unverified. Never invent current prices, opening times or availability.

Provide requested details or a detailed itinerary in this chat, with sources
and uncertainties. Lists and tables are welcome when helpful. A details request
does not increase the search budget or authorize email. Only when the user
explicitly asks to send email, use `email-results`.

For flight information, use `flight-search`. Do not apply this travel procedure
to unrelated questions.
