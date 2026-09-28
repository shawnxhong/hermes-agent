# Clear conversation without a cold prompt boundary

Status: implementation on laptop; parent `baseline/docker-20260920.1`.
Do not change demo-site box_a. Docker is authoritative.

## Contract

- CLI `/clear` and voice context clear discard conversation/task state, not
  the current persona, tools or selected scene. Rotate session identifiers,
  archive the old transcript and preserve the existing lifecycle hooks.
- Retain the Agent/client. The new session must have a populated system prompt
  before its first user request when an Agent has already been initialized.
- A scene exit/switch must remove the old scene. Do not reuse its full prompt.
  `/new`, model changes and the IM Gateway retain their existing behavior.
- Preserve cancellation, media stop, generation fencing and wake restoration.
  Never send a dummy user request, synthesize a reply, or replay old tools.

## Implementation

Use the existing session reset lifecycle, adding an explicit clear-only prompt
preparation option. Refresh at the idle clear boundary after session-reset hooks,
using the existing prompt builder and first-session initialization/persistence
path. This validates all sources rather than inventing an incomplete fingerprint
of SOUL, skills, plugins, model settings and context files. If the rendered bytes
are unchanged, retain the previous string/static prefix; otherwise keep the newly
rendered prompt, including fresh date/session identity. Never cache message/tool
history or restore a previous scene's prompt.

This is a deliberate correctness tradeoff: a local render still occurs during
clear to validate changing sources, but not on the next user turn. It does not
claim to eliminate OVMS prefill. Hermes prompt-object caching and server KV/prefix
caching are separate; an identical prompt alone cannot guarantee warm inference.
No model request or GPU warm-up is added by clear.

On preparation failure, discard the candidate and fall back to ordinary lazy
construction. Log only timing, lengths and reuse outcome, not prompt contents.
An uninitialized CLI remains lazy; clear must not load the entire Agent solely
to warm it. Scene switching remains a full reset in this change, including panel
toggle-off, which is a scene exit rather than ordinary context clear.

## Tests and release

1. Commit this plan before implementation in a task worktree.
2. Regression: unchanged prompt retained; new session/history/todo state clean;
   changed sources refreshed; preparation failure fails safely; `/new` unchanged;
   clear before initialization; prompt persisted under the new session only;
   session-start/reset hooks not duplicated on the next turn.
3. Existing scene/voice-command/session/prompt tests through scripts/run_tests.sh.
4. Overlay only reviewed changed files on the pinned CLI image; isolated Docker
   smoke with temporary state and no production sockets/credentials. Record
   source commits, image IDs, tests and performance limitations in ops.
5. Human acceptance remains required. Do not advance the accepted baseline or
   update box_a. Candidate rollback uses the unchanged parent image and state;
   no schema migration or data deletion.
