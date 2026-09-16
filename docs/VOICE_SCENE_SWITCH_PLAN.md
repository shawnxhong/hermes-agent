# Voice scene switching via touchscreen buttons

Date: 2026-09-16
Status: approved design; implementation has not started.
Owner: laptop. Integration branch: `local-ovms-voice`.

## User experience and scope

The final appliance runs on one box without a conventional display. A small
touchscreen provides four scene buttons, currently observed by a Bash script.
Hermes CLI remains running in its terminal throughout scene switches. Gateway
IM remains independently available and retains its conversations.

For every accepted button press:

1. Interrupt current voice output and suspend wake detection and ASR immediately.
2. Play **"One moment please"** promptly, without waiting for the old model turn,
   tool request, context clearing, or skill loading to finish.
3. Cancel the old voice task, clear its conversation and transient state, and
   activate the selected scene skill.
4. After both the acknowledgement playback and reset/loading have completed,
   play the scene-ready announcement; for travel the exact phrase is
   **"Travel assistant ready."**
5. After ready playback finishes and the existing audio-tail guard completes,
   resume wake listening. The user must say **"Hello Intel"** to start speaking.
   Do not automatically start ASR or play a follow-up recording cue.

Selecting the current scene again starts a fresh conversation in that scene.
Scene switching never emits the process startup or shutdown announcements.
All user-facing announcements are English.

## Local control interface

Proposed entry point for the existing button script:

```bash
hermes-box-scene switch <scene-id>
```

Use a same-user Unix socket owned by the active voice CLI, scoped to its Hermes
profile/session. Reuse existing control infrastructure where suitable. Do not
simulate terminal keystrokes or ask the LLM to interpret button commands.
Restrict socket access to the owning user; select exactly one voice target.
If no target is running, report an error rather than launch another instance.

Keep scene mappings in versioned non-secret configuration, with host-specific
paths supplied at deployment. Behavioral configuration belongs in YAML, not
new public environment variables. Illustrative mapping only:

```yaml
scenes:
  travel:
    skill: travel-planning
    announcement: "Travel assistant ready."
```

The actual four scene IDs, installed skill identifiers, and button script path
must be inventoried before implementation. Do not invent mappings for the
remaining buttons. Acknowledging command acceptance and reporting readiness
are distinct statuses; acceptance must not claim that loading has finished.

## Immediate acknowledgement and audio ownership

Pre-render/cache the fixed acknowledgement and configured ready phrases through
the local TTS path outside the button critical path. No cloud service or model
generation is needed for a button acknowledgement. Validate audio availability
when enabling the integration; keep generated audio out of Git.

On an accepted press, invalidate old-turn output before starting acknowledgement
playback. Stop the CLI-owned player and purge its old queued audio. Give control
announcements priority over ordinary response audio. Reset/cancellation work may
run while "One moment please" plays, but ready playback must wait for both.
Use one serialized audio owner: no overlapping old TTS, acknowledgement, or ready
speech. Scope playback cancellation to this CLI; never kill global audio players
or interrupt gateway-owned activity.

Keep wake listening, recording, transcription submission, and automatic
follow-up recording disabled throughout the switch. Apply the existing echo/tail
protection after announcements before enabling wake detection.

Debounce hardware duplicate events. During rapid genuine presses, the latest
valid scene wins. Coalesce acknowledgement while it is already playing; do not
queue a spoken phrase for each event. Cancel superseded ready announcements.
An isolated new press, including a press of the current scene, gets an
acknowledgement. All asynchronous switch callbacks check the current switch ID.

## Reset, cancellation, and stale-result isolation

The voice process survives. Start a new conversation generation using the
existing `/clear` lifecycle rather than duplicating its state reset logic.
Audit that lifecycle before implementation: reset history, pending clarification
or permission state, partial recordings/transcripts, queued responses, per-turn
workflow state, and active scene instructions. Preserve durable memory, files,
credentials, configuration, and independent IM history.

Associate model streams, tool work, transcription, playback, and queued delivery
with a conversation generation. Increment it as soon as a valid switch is
accepted. Stale work cannot append to the new conversation, schedule another
tool, submit queued mail, or play speech. Also isolate mutable per-turn state;
discarding display output alone is insufficient.

Cancel model streams, owned recording/playback processes, and cancellable tool
tasks. For requests that cannot be cancelled promptly, detach their results from
the new session. Do not wait indefinitely for them before becoming ready, but
do not claim they have stopped if they are still executing. Audit shared resource
ownership and apply bounded cancellation deadlines during implementation.

Cancel mail that has not begun delivery. Mail already submitted, device actions
already executed, and external operations past their commit point cannot be
undone by clearing a conversation. Do not automatically retry them. Preserve
their delivery/action receipts independently of the cleared conversational text.

## Skill activation and prompt contract

Harness changes are limited to generic session cancellation/reset, scene
activation, control transport, and audio lifecycle. Domain behavior remains in
skills. Do not add scene-specific planning rules or model tool schemas.

Activate only the selected scene's instructions, exactly once, in the new
conversation. Remove the previous scene activation. Preserve the normal skill
discovery mechanism; do not introduce a scene tool allowlist or prohibit unrelated
user questions. Keep the system prompt stable during each conversation; switching
creates the new boundary at which its scene context may change.

## Failure behavior

Validate scene ID, skill availability, and required configuration locally before
destructive clearing. Keep this fast so acknowledgement remains immediate for
valid button events. Invalid mappings return an error without clearing the
current conversation or announcing readiness.

After acceptance, a reset/load failure must never produce a ready announcement.
Use a short English failure announcement when audio works, log the cause, and
restore a coherent wake-listening state. Preserve the previous scene if reset
has not committed; otherwise use a clean general session. Never restore an old
in-flight task. A subsequent valid press must remain usable.

If announcement playback fails, record it and restore wake listening rather than
leave the device stuck in a switching state. Model/network failure must not block
the local control channel. Test this with the existing offline fallback enabled.

## Multi-machine implementation sequence

- laptop owns scene switching and develops in a dedicated task branch/worktree.
- box_a owns latency optimization; record timings usable by that workstream.
- box_b owns application prompt reduction; agree on one scene injection entry
  point and its reset semantics before overlapping implementation changes.

GitHub remains authoritative. Develop from current `origin/local-ovms-voice`,
review and merge via the established workflow, and deploy reviewed files from
the integrated commit with a per-host deployment receipt. No remote deployment,
service restart, or runtime patch is part of this design-document task.

Implementation stages:

1. Inventory the four buttons/skills, existing `/clear`, interrupt handling,
   voice playback ownership, and available local control transport. Identify
   overlap with box_a and box_b before editing shared components.
2. Implement the generic controller, request IDs, generation isolation, and
   bounded cancellation. Integrate the Bash command interface.
3. Add prioritized cached acknowledgement/ready playback, wake gating, debounce,
   and last-request-wins behavior. Configure actual scene mappings.
4. Run focused automated lifecycle tests and local manual button/voice checks.
   Merge reviewed code and deploy with a manifest only during implementation.

## Acceptance checks

- Switch while idle, recording, transcribing, generating, executing tools, and
  playing TTS; old output stops and cannot resume in the new generation.
- Hear "One moment please" before slow reset/tool completion. Hear the target
  ready phrase only after loading succeeds, with no overlapping speech.
- After readiness, silence causes no ASR; "Hello Intel" starts normal interaction.
  Neither announcement wakes or records itself.
- Rapid presses retain only the final scene; duplicate hardware signals do not
  produce repeated speech. Pressing the same scene intentionally resets it.
- New context has no old history/scene instructions or transient pending actions;
  the selected skill is injected exactly once and ordinary questions still work.
- Delayed tool/transcription callbacks and queued mail cannot cross the reset
  boundary. Already-submitted side effects are accurately recorded.
- Missing skill, playback failure, uncancellable request, and offline operation
  do not strand the CLI; a later valid button press works.
- CLI process identity stays unchanged; gateway IM conversation and service
  continue functioning throughout the switch.
- Capture button receipt, old-audio stop, acknowledgement start/end, reset/skill
  completion, ready start/end, and wake re-enabled timestamps. Proposed local
  acknowledgement-start target: within 500 ms of accepted input with cached
  audio. Treat it as a measurement target until verified on box hardware.

This document records design requirements, not verified implementation behavior.
