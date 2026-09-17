---
name: flight-search
description: Find brief flight route information and publicly advertised fares on Expedia; no booking or payment.
---

# Flight lookup

Use English. Read departure city, destination, dates and one-way/return intent
from this conversation. Ask one short ordinary question for essential missing
details; never repeat information already supplied. Resolve relative dates from
the current date; clarify only genuinely ambiguous dates.

Make one `web_search` restricted to `site:expedia.com` with the route and dates.
If useful, read one matching result with `web_extract`. Use no other site or
browser automation. On access failure, stop and briefly say what could not be
checked.

Report at most two supported options in 1–3 spoken sentences. State the airline,
route/stops and price/currency only when present in the retrieved evidence.
Public "from" fares are reference prices, not confirmed quotes for the user's
dates. If exact dates or availability cannot be verified, explicitly say so;
do not invent flight numbers, schedules, direct flights or available seats.

Do not book, reserve, log in or pay. Do not email automatically. If explicitly
asked to email results, load `email-results`, include the source links and
limitations in the email, and give only a short spoken status.
