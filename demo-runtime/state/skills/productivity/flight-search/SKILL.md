---
name: flight-search
description: Find one flight option and requested details.
---

# One-way flight lookup

This is the complete skill body, already loaded. Do not call `skill_view`
again for this skill or its `SKILL.md`. Reuse it on follow-up turns.
While tools are needed, call them without narrating plans or giving a draft
answer. Finish the necessary lookup and any requested details submission first,
then give one final reply in 1–3 short sentences, with no headings or lists.
Do not answer and call another tool in the same response. If essential facts
are missing, ask one short question instead, then wait for the user's reply.

Reply in English, in 1–3 short sentences. Default to one one-way flight option,
not a comparison or return itinerary. Reuse known origin, destination and date;
resolve relative dates using today. Ask one ordinary short question only for
essential missing facts. Do not ask for cabin, budget or airline preferences.

Make one `web_search` restricted to `site:expedia.com` for the route and date;
extract at most one matching page if needed. On failure, stop; no other sites
or browser escalation. Give airline, stops, flight number, time and fare only
where supported. If only a route or advertised "from" fare is available, say
so: it is not a verified flight or date-specific price. Never invent an option.

Only when the user actively requests detailed information, prepare those
details. In voice, call `deliver_details` once with a short `title` and detailed
`content`, including sources and verification limits. The service selects the
only enabled destination. Include actual findings, not just a heading. If the
user requires a different channel, explain it is unavailable and do not send.
For IM, reply with details directly unless external delivery is requested.
Give a brief summary and mention the returned channel only after `queued`.
Do not wait for delivery, poll, retry, or read
the body aloud. Report rejected or unconfirmed submissions briefly.
Respect an explicit request not to send.

Do not book, reserve, log in or pay. Do not apply this workflow to other tasks.
