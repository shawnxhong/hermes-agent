# Travel and home scene buttons

The voice CLI stays alive across switches. Gateway IM sessions are unaffected.
On a laptop without a touchscreen, run these commands from another terminal:

```bash
hermes-box-scene switch travel
hermes-box-scene switch home
hermes-box-scene status
```

These are also the two commands for the future touchscreen Bash listener.
`switch` reports acceptance, not readiness. Status contains the owning PID,
active/pending scene, switching flag and last error. A missing CLI returns an
error without spawning a duplicate process.

The interaction is: stop old output, say **One moment please**, reset the
conversation and replace the scene skill, say **Travel assistant ready.** or
**Home assistant ready.**, then wait for **Hello Intel**. The button never opens
an automatic recording window. Re-selecting the active scene resets it again.
Hardware duplicates within 300 ms are ignored; rapid different presses select
the last scene and coalesce an acknowledgement already playing.

## Installation

Install `scripts/local-ovms/hermes-box-scene` in the user's executable path.
Enable these settings in the target profile's `config.yaml`:

```yaml
voice:
  scenes:
    enabled: true
    items:
      travel:
        skill: travel-concierge
        announcement: Travel assistant ready.
      home:
        skill: demo-home-assistant
        announcement: Home assistant ready.
```

Both skills must be installed. The home skill, native plugin and independent
loopback simulator already live under `scripts/local-ovms/skills`, `plugins`, and
`hermes-demo-home.service`. Install those assets, enable `demo-home` in plugins,
and include `demo_home` in CLI platform toolsets. Home state is durable and is
not reset by scene switching. All device changes are simulated.

Run `hermes-box-scene prepare` to pre-render local announcement audio. Restart
the voice CLI once after deploying code; subsequent switches do not restart it.
The control endpoint starts only when scene control is enabled and this CLI
owns an active wake listener. Socket/lock files are profile-local under `run/`.

## Cancellation boundary

The implementation uses hard interruption, waits for the current agent worker
to unwind, then runs the existing new-session lifecycle on the CLI processing
thread. It deliberately does not reset an agent still executing a tool. Old
transcription and batch TTS results carry a generation check; follow-up and wake
capture are blocked during switching. Streaming TTS receives its stop event.

Queued voice emails associated with the old session are cancelled atomically;
already-running deliveries keep their receipts and cannot be recalled. Existing
queue records created before the session mapping was introduced cannot be
reliably attributed and are left untouched.

If an external tool ignores cancellation, acknowledgement is immediate but
readiness waits for that worker to return. This is a known limitation versus the
design's proposed bounded detachment; safe detachment of all shared agent state
is not implemented. Never treat an accepted request as a ready scene. Model API
and ordinary terminal tools use Hermes' existing interruption support.

Validation must include the actual installed CLI and manual acoustic checks;
unit tests alone do not establish microphone or speaker reliability.
