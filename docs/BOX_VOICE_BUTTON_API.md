# Intel AI Box voice button API

Linux host integration; no model-facing tools, prompts or IM changes.
IM gateway remains the boot-enabled system service `hermes-gateway.service`.
Local voice uses the separate user service `hermes-box-voice.service`, which
is NOT boot-enabled and is NOT automatically restarted after failure.

## Button integration

Run as the audio user (`intel` on the Box):

```sh
hermes-box-voice start
hermes-box-voice stop
hermes-box-voice toggle
hermes-box-voice status
```

One JSON response; exit code 0 on success, 1 on error. States: off, starting,
ready, stopping, failed, unavailable. `ready` means initial wake setup finished;
the service may currently be listening, recording or answering.
Start/stop wait for completion: invoke asynchronously from the screen UI.
Repeated start while ready and stop while off do nothing and play no audio.
Debounce physical button events in the screen application, especially toggle.
Mutations serialize to avoid duplicate voice processes. Do not also run a
manual `hermes-mode voice --run` while this service owns the microphone.

An existing root-owned screen app can select the audio user explicitly:

```sh
runuser -u intel -- env XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  /home/intel/.local/bin/hermes-box-voice start
```

Do not grant arbitrary passwordless sudo or expose this interface to a network.
This is a software integration interface, not a driver for an unspecified keypad.

## Audio lifecycle

The supervisor gives the existing CLI a private PTY. CLI opens the wake listener,
pauses it, plays **Intel AI Box agent standing by**, waits 250 ms, then re-arms.
Only afterward is the service marked ready. The initial-readiness guard prevents
replays during later wakes, re-arms, follow-up questions and normal replies.

Requested stop terminates the owned CLI process group and waits for capture and
playback cleanup before playing **See you next time**. IM is never signalled.
Startup failure or unexpected CLI exit does not play a successful shutdown cue.
Forced kill, power loss or audio failure cannot guarantee an audible cue.
Normal OS/user-manager shutdown also closes active voice and may play goodbye
if the audio service remains available. No automatic restart means no cue loop.

## Installation and configuration

Install the script to `~/hermes-ovms-setup/hermes-box-voice.py` (executable),
link `~/.local/bin/hermes-box-voice` to it, and install the adjacent service
under `~/.config/systemd/user/`. Run `systemctl --user daemon-reload`;
**do not enable** the voice service. A working user manager/PipeWire session
is required (linger is enabled on this Box).

Run `prepare-box-voice.py` using installed Hermes Python. It generates local
Kokoro clips in `$HERMES_HOME/cache/box-voice/`, without playing them.
Set `voice.startup_cue_file` to the absolute path of `standby.wav` in the
Box config. `goodbye.wav` must also exist in that cache directory.
Preserve existing voice/wake settings. Start does not rewrite them.
The optional startup file also replaces the first readiness beep for manual
voice launchers on this host; it is never injected into model context.

Diagnostics:

```sh
systemctl --user status hermes-box-voice.service
journalctl --user -u hermes-box-voice.service -n 50
tail -n 100 ~/.hermes/logs/box-voice.log
```

The bounded rotating log can contain CLI transcripts and is user-private.
Rollback: stop the voice service, remove its unit/launcher, daemon-reload and
remove `voice.startup_cue_file` to restore the original first readiness beep.
Stopping voice does not clear history or change power, OVMS or IM settings.
