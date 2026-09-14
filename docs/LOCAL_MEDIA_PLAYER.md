# Local MP3 / MP4 playback demo

This isolated skill uses native Hermes terminal execution. No changes to
core, tool definitions, model settings, wake detection or general voice policy.
Fixed assets: `~/hermes-demo-media/demo.mp3` and `demo.mp4` (desktop user's home).
The MP3 is local Kokoro English narration; MP4 is a 720p title/waveform card
with that narration. Both are fully offline at playback time.

## Install / migrate

On Ubuntu install ffmpeg if absent (provides ffplay), and use an active desktop
session with its user systemd manager. Copy the skill directory from
`scripts/local-ovms/skills/local-media-player` into the actual profile's
`skills/media/local-media-player`. Copy the two generated assets into the
desktop user's `~/hermes-demo-media/`. No Python packages, credentials or
model changes are required by the helper. No new boot service is installed.

Load the skill in a new Hermes session. For an explicit preloaded smoke:
`hermes --cli --skills local-media-player`. Normal skill discovery also exposes
its description. Existing already-open sessions may need a normal restart.

Try "Play the demo audio", "Stop the audio", "Play the demo video", or
"播放视频". Only explicit media tasks use this skill. IM plays media on
the Ubuntu desktop, not on the phone. Player startup does not prove audibility.

The helper starts a transient `hermes-demo-media.service` owned by the desktop
user. It exits with the clip and never stops unrelated players. Check with
`systemctl --user status hermes-demo-media.service`; stop with
`systemctl --user stop hermes-demo-media.service`.

## Generate assets

Generate `narration.wav` with the existing local Kokoro helper using
`scripts/local-ovms/media-demo-narration.txt`. Then run:

```bash
python3 scripts/local-ovms/prepare-media-demo.py --wav /path/to/narration.wav
```

Generation refuses to overwrite existing MP3/MP4 assets. Deployment preserves
existing files; back them up before an intentional replacement. Binary media
is delivered separately from Git, alongside the skill in the migration bundle.

## Scope / limits

- English-first skill with Chinese triggers; no model-generated media paths.
- This version plays only the fixed demo pair, not arbitrary paths or URLs.
- Device routing/volume and ASR/TTS coordination are unchanged. A brief spoken
  acknowledgment may overlap playback; test this on the final microphone setup.
- Failure or missing desktop/file is reported, not silently converted to success.
