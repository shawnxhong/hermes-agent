# Black Box Feishu integration handover

Updated 2026-10-15. Delivery is ONLY the branch
work/black_box/docker-20261015.3 in BOTH hermes-agent and hermes-demo-ops.
The branch name is a task identifier, not a published Docker release.
User explicitly requested deletion of docker-20261015.3 tags in both repos.
Do not recreate or upload those tags. Preserve docker-20261015.2 tags.

## Source and scope

Base: refs/tags/docker-20261015.2, app
f59a3a72f2d4d90ccea1b5302b76d908d1d3803a.
App integration: f9d3447c0d3553d661a52ee5d67689696a7443d0.
Source tree before this handover: e24ddefef7ee277dd3589e25347bea42246584a5.
Companion ops integration: 879abb0ecd34e5a13cfe3fdf583c6836869742f0,
plus later handover commits on the same branch name.

The SDK 0.4.0 and email integration from .2 remain intact. App changes add
Gateway SOUL defaults requiring a fresh local_media stop call and the current
tool result before claiming playback stopped. Existing .2 media plugin and
skill are retained; old experimental core/router code was not merged wholesale.
No credentials, models, pairing, chat history or private logs belong in Git.

Ops supplies the independent pinned Feishu deployment, strict no-GPU/no-mic
coexistence policy, media synchronization, committed skill/SOUL migration and
targeted tests. The new sync helper reads installed systemd production media
bindings instead of hardcoding a historical baseline. Use the companion ops
branch handover at handoffs/20261015-black-box-docker-20261015.3.md for full
operations, deployment instructions, pairing commands and rollback boundaries.

## Runtime is separate from this branch

Black Box voice currently selects /home/intel/hermes-runtime/docker-20261015.1.
Independent paired Feishu runs under
/home/intel/code/deployments/feishu-im-20261014 with Gateway image
sha256:2e8cfecced2b89fc83167230ee1ac478dbe6720f55e7b29ea69cc33ca0d99f76.
Voice CLI 550df0961345, wake 768b56ff47e1, ASR/TTS/home 073858d56f91,
and OVMS cc13f88dc249 were running at handover verification.

Complete accepted fleet release remains .2 in ops releases/CURRENT.json.
No integrated .3 image set was built or deployed. The branch's new dynamic
media sync helper is NOT installed in the currently running .1 environment.
Active Feishu SOUL already has the tested confirmation rules, SHA256
121ae8c6bc0968eea9e4a42bef6e76e3691213e74a4b209ca3a1035b7cc58de3.

## Evidence and limits

22 isolated Docker ops tests passed. Source Git asset reading, production media
root discovery and local/remote app tree equality were checked. Real local-model
checks used the actual saved Gateway prompt and full tool names, including
cronjob_manage; final rules selected media stop for music/video/repeated music.
A real audio player went active, then the model-selected stop executed and
status became inactive with PID zero and no members. Tests sent no IM/email.

Earlier real Feishu replies were false positives: old history repeated a cronjob
guardrail error; after a reset, another reply claimed Music stopped with zero
tool calls while ffplay remained active. Those failures led to the SOUL fix.
Post-fix actual incoming Feishu human acceptance remains unconfirmed. A direct
model/tool probe does not prove the entire inbound chat path. Physical audio,
video/panel and reboot acceptance remain pending.

## Next steps

Fetch the two integration branches, preserve .2 SDK/email behavior and existing
private state, then build/test complete candidates from pinned .2 inputs.
Explicitly migrate persistent skill/SOUL and media bindings with backups; obtain
real chat/audio acceptance before any future baseline promotion. Do not publish
.3 tags as part of this branch-only handoff. Assets unchanged; other hosts untouched.

App branch uploads use the authorized GitHub connector; SSH user wenyi5608 has
no app write access. Ops source/handovers are uploaded on the companion branch.
This handover update changes no running service, credential or host settings.
