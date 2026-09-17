---
name: flight-search
description: One brief one-way flight option; email requested details, never book.
---

# One-way flight lookup

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
details and email the configured default recipients. For this scene, asking
for details authorizes that email; an ordinary flight lookup does not.
Use `send_message` with `action="send"`, `target="email"`, and the details in
`message`. Call it directly. Include sources and verification limits. Give only
a brief summary and truthful delivery status in the reply. Do not retry an
uncertain delivery. Respect an explicit request not to email.

Do not book, reserve, log in or pay. Do not apply this workflow to other tasks.

Do not call `clarify` or `approve`. Ask essential questions in an ordinary reply.
Do not bypass any permission check required by the tool or platform.
