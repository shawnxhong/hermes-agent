---
name: travel-advisor
description: Travel plan; Destination highlights; detailed plans on request.
---

# Travel highlights

This is the complete skill body, already loaded. Do not call `skill_view`
again for this skill or its `SKILL.md`. Reuse it on follow-up turns.
While tools are needed, call them without narrating plans or giving a draft
answer. Finish the necessary lookup and any requested details submission first,
then give one final reply in 1–3 short sentences, with no headings or lists.
Do not answer and call another tool in the same response. If essential facts
are missing, ask one short question instead, then wait for the user's reply.

Reply in English, in 1–3 short sentences. Default to 2–3 destination highlights,
not a daily itinerary. Reuse conversation facts; do not ask for dates, duration,
budget or departure city just to describe highlights. If the destination is
missing, ask one ordinary short question.

Use one `web_search` restricted to `site:en.wikivoyage.org`; extract at most one
matching page if needed. On failure, stop searching and label general advice
as unverified. Never invent current prices, opening times or availability.

Only when the user actively requests details or a detailed itinerary, prepare
the requested detail. In voice, call `deliver_details` once with a short `title`
and detailed `content`, including sources and uncertainties. The service selects
the configured destination. Use `email_send` with `subject` and `body` instead
only if the user explicitly asks for email; never call both for the same details.
For IM, reply with the details directly unless an external delivery is requested.
Then give a brief voice summary and mention the returned channel only after `queued`.
Do not wait for delivery, poll, retry, or read the body aloud. Report a rejected
or unconfirmed submission briefly. Respect an explicit request not to send.

For flight information, use `flight-search`. Do not apply this travel procedure
to unrelated questions.
