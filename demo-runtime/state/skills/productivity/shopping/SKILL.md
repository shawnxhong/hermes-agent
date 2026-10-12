---
name: shopping
description: Find one product option from US Walmart.
---

Find one product matching the user's stated needs and budget. If the product
type is missing, ask one short question.

Search once with `web_search` restricted to `site:walmart.com`, then read
one matching product page with `web_extract`. Use facts for that exact variant,
not snippets. If unavailable or unverified, say so and stop; no other sites.

Reply with the name and one matching feature in 1–2 sentences; include the link
for text chat. Quote a price only if verified; never guess stock or delivery.
No buying, cart changes, medical treatments, email, or unsupported "best" claims.
