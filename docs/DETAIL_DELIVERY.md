# Configurable details delivery

CLI plugin `email-queue` registers only `deliver_details(title, content)` in
toolset `detail_delivery`. Enable that toolset and run the separate outbound-only
detail_delivery.py worker using the same private HERMES_HOME as the CLI. Mount
state/detail-queue into both. Do not start the old mail worker or enable its toolset.
Start the details worker before CLI discovery; its active.json provides the fixed
channel in the tool description. The tool has no channel/recipient parameters.
No router, summary pass, core loop changes or Gateway startup is needed.

```yaml
detail_delivery:
  channel: feishu # or email; absent means feishu
  feishu:
    target_type: chat_id
    target_id: oc_YOUR_DEVELOPMENT_GROUP
```

Feishu credentials: FEISHU_APP_ID and FEISHU_APP_SECRET in the worker's private
.env. Use the existing application's credentials, with bot send permission and
membership in the target group. Secrets/group binding are NOT image/source defaults.
Email mode uses the existing default-recipient configuration and SMTP adapter.
Channel, target and credential changes require a full service stop/start, including
the CLI. The worker freezes its backend/recipients at startup, not per submission.
An explicit user request for an unavailable channel must not silently send elsewhere.
Only administrators edit configuration, never the conversational agent. No fallback.
Pending jobs for a different target block startup; keep them for operator review.

Details have their own SQLite DB/socket under detail-queue, preserving mail queue
format and rollback separation. One default-details job per profile/session/turn;
the original destination is returned for duplicates even after a config change.
Tools reject closed/released turns; accepted jobs remain committed after a reset.
No late model callback may create a new accepted task from a closed turn.
The legacy mail queue remains untouched and is not drained by this worker.

Feishu chunks are UTF-8 bounded text messages. Local job/part receipts record each
acknowledged message ID; deterministic per-job part UUIDs are sent to Feishu.
Transport errors after submission are unknown, never automatically retried.
Partial sends stop, do not resend successful parts. Interrupted jobs are unknown
and not replayed at restart. This is conservative at-most-one attempt, not an
exactly-once or guaranteed-delivery claim. Shared bot does not mean shared queues:
each device owns independent state; no cross-device dedup is promised.

API reference: https://open.feishu.cn/document/server-docs/im-v1/message/create

Rollback: stop CLI and detail worker, retain detail-queue DB and matching images;
restore old CLI prompts/toolsets/image. Never point an old mail worker at the
detail-queue DB or automatically requeue failed/unknown deliveries.
