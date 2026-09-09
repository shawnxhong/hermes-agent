# Spoken answer-window cue

Deployed and enabled on 2026-09-09 from pushed commit `60f0b0bbb`.
Per-file backup: `/home/agentdemo/hermes-ovms-setup/backups/20260909_104111-voice-ready-cue`.
Installed clarify/permission barrier→cue→capture smoke checks passed; unrelated
configuration was compared against the backup and is unchanged.

A local CLI-only 200 ms, 660 Hz tone plays after the question TTS barrier, a
configurable 650 ms quiet gap, and
before microphone capture, for ordinary follow-up, clarify and permission.
No model call, system prompt change, IM sound or new permission policy is added.
The existing faded/bounded audio player and voice.beep_volume are reused.
The cue has its own enable switch, separate from ordinary record/stop beeps.
When disabled, prior beep behavior is retained.

On the first voice activation in each CLI process, cached local TTS says:
"After the tone, you can answer directly." It is not repeated on each question.
The introduction can be disabled or localized without changing the tone.

```yaml
voice:
  ready_cue:
    enabled: true
    intro_enabled: true
    pre_gap_seconds: 0.65
    intro_text: "After the tone, you can answer directly."
```

No answer retains the existing bounded silence timeout and wake waiting; empty
transcripts are not submitted. Cancellation during the ordinary follow-up cue
prevents capture. Audio failure is best-effort and does not break voice handling.
A tone is not a guarantee of microphone hardware health: human testing is still
required for audibility, speech onset capture, and the room's acoustic echo.

Validation: 81 focused tests cover cue selection, disabled behavior, failure,
once-only introduction, question/barrier/cue/capture order in all three paths,
cancellation, silence and wake regression. Local cue playback returned without
error, and the cached English introduction generated and played successfully.
Restart hermes-voice to load this change; no Gateway/model restart is needed.

## Manual-test corrections (2026-09-09)

The automatic wake startup bypasses `/voice on`; its listener startup now also
runs the introduction before opening the wake microphone, honoring auto-TTS.
The once flag is set only after successful playback, so failed playback may be
retried on a later activation. A regression exercises the actual CLI wake-start
method, not just the standalone intro helper. The quiet gap distinguishes the
answer cue from the last TTS syllable (range 0–2 seconds).
