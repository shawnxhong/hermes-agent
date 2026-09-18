---
name: flight-search
description: Find flight option; email details on request.
---

Find one one-way flight option. Reuse known origin, destination and date;
resolve relative dates using today. Ask only for essential missing facts.

Search once with `web_search` restricted to `site:expedia.com` for the route
and date; use `web_extract` on at most one matching page if needed.
On failure, stop. Report only supported flight details; a route or "from" fare
is not a verified dated flight. Never invent availability or prices.

Only a request for details authorizes email; respect "no email".
Call `email_send` once with only `subject` and `body`, including sources and
uncertainties; recipients are automatic. Say "queued for email" only after
a queued result; report failure briefly. Do not poll or retry.
Finish with a brief summary, not the email body. Never book, reserve, log in or pay.
