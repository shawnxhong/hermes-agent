# Web/email repair, session 20260917_115840_f97087

No harness changes. Wikivoyage search succeeded; extraction was rejected by
the URL safety guard because local Clash Fake-IP DNS returned 198.18.0.17 and
a private IPv6 address. Domain-specific real resolution and clearing both
proxy and system DNS caches restored actual extraction of the Tokyo page.
SSRF protection stays enabled. Host changes belong in the ops receipt.

Email failed before SMTP: the model used target=send_message instead of
target=email, then repeatedly described the same tool. The email skill now
shows valid direct and bridge JSON, distinguishes tool name from target,
permits one correction only for pre-delivery parameter rejection, and forbids
retrying an uncertain delivery. Transport, permissions and retry machinery
are unchanged. Skill instructions improve guidance, not a hard retry boundary.

The media skill's seven-line automatic-review addition is removed, restoring
its content exactly to 4db3b4483. Native auxiliary.background_review.enabled=false
disables automatic review; explicit /refine remains available. Title generation
and foreground skill editing are not disabled by that setting.

Validation: 67 existing tests passed across media, background review cost
controls and message sending. The real OVMS email probe uses captured dispatch
and asserts exactly one send, target=email and the requested message content.
The one real SMTP test returned success for the two configured recipients;
actual inbox receipt requires human confirmation. No automatic re-send.
