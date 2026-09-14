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

For stronger local-Qwen routing, preload in a new Hermes session:
`hermes --cli --skills travel-concierge,local-media-player` (or only
`local-media-player` if travel is not wanted). Normal skill discovery exposes
its description but was not reliable with the full tool catalog: Qwen sometimes
skipped skills and tried desktop controls or another player. Adding synonyms
alone does not guarantee discovery. Native preload changes no harness code.
Full-tool tests also showed occasional unwanted GUI verification after player
startup, even with preload. This release broadens language handling but is NOT
a fully reliable fix for model tool selection. Do not report the full-tool
English playback acceptance as consistently passing.
An already-open session retains its original prompt; restart it normally.

Try "Play the demo audio", "Stop the audio", "Play the demo video", or
"播放视频". Only explicit media tasks use this skill. IM plays media on
the Ubuntu desktop, not on the phone. Player startup does not prove audibility.
"play the mp4 video", "MP four video", "put on some music" and Chinese
equivalents select fixed defaults without asking for a filename. A named
different file, song/movie or URL is not silently replaced with the demo.

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
