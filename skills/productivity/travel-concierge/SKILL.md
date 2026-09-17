---
name: travel-concierge
description: Brief destination highlights; email a detailed plan only when requested.
---

# Travel highlights

This is the complete skill body, already loaded. Do not call `skill_view`
again for this skill or its `SKILL.md`. Reuse it on follow-up turns.
While tools are needed, call them without narrating plans or giving a draft
answer. Finish the necessary lookup and any requested email delivery first,
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
the requested detail and send it to the configured default email recipients.
For this scene, that request authorizes emailing the details; a general request
for suggestions does not. Use `send_message` with `action="send"`,
`target="email"`, and the detailed content in `message`. Call it directly.
Include sources and uncertainties. Reply with a brief summary and truthful
delivery status, not the email body. Never claim success after a failed send
or retry an uncertain delivery. Respect an explicit request not to email.

For flight information, use `flight-search`. Do not apply this travel procedure
to unrelated questions.