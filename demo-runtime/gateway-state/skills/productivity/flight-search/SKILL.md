---
name: flight-search
description: Find one one-way flight option; provide requested details in chat.
---

# One-way flight lookup

This is the complete skill body, already loaded. Do not call `skill_view`
again for this skill or its `SKILL.md`. Reuse it on follow-up turns.
While tools are needed, call them without narrating plans or giving a draft
answer. Finish the necessary lookup before one final reply.
Do not answer and call another tool in the same response. If essential facts
are missing, ask one short question instead, then wait for the user's reply.

Reply directly in English. Default to one one-way flight option,
not a comparison or return itinerary. Reuse known origin, destination and date;
resolve relative dates using today. Ask one ordinary short question only for
essential missing facts. Do not ask for cabin, budget or airline preferences.

Make one `web_search` restricted to `site:expedia.com` for the route and date;
extract at most one matching page if needed. On failure, stop; no other sites
or browser escalation. Give airline, stops, flight number, time and fare only
where supported. If only a route or advertised "from" fare is available, say
so: it is not a verified flight or date-specific price. Never invent an option.

Provide requested details in this chat, with sources and verification limits.
Details do not authorize email or extra searches. Only when the user explicitly
asks to send email, use `email-results`.

Do not book, reserve, log in or pay. Do not apply this workflow to other tasks.
