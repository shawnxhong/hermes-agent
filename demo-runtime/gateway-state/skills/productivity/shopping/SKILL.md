---
name: shopping
description: Find one product option from US Walmart.
---

Find one product matching the user's stated needs and budget. If the product
type is missing, ask one short question.

Search once with `web_search` restricted to `site:walmart.com`, then read
one matching product page with `web_extract`. Use facts for that exact variant,
not snippets. If unavailable or unverified, say so and stop; no other sites.

Reply with the name, one matching feature and the link by default. Provide requested
details in this chat; details do not authorize extra searches or email.
Quote a price only if verified; never guess stock or delivery.
No buying, cart changes, medical treatments or unsupported "best" claims.
Only an explicit email request is handled by `email-results`.
