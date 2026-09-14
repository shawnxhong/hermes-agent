# Desktop media playback

Current skill v0.4 lists and plays real media files directly in `~/Desktop`.
The old demo.mp3/demo.mp4 defaults are no longer required or preferred.
Adding/removing media files needs no restart; changing plugin code does.

## Architecture

The existing `demo-media` native plugin recognizes media commands, asks the
local model once for an action and a user-supplied title fragment, and invokes
the installed helper. `run_turn_workflow` returns a handled result immediately:
there is no post-playback model/GUI verification loop. Main harness, model,
system prompts, general voice delivery and unrelated tasks are unchanged.

`local_media` actions: `list`, `play`, `mp3` (audio), `mp4` (video), `stop`,
`status`. `target` is an optional filename/title fragment. Missing/ambiguous
matches start nothing; one matching file starts immediately. The helper scans
each call, excludes hidden/empty files and symlinks, accepts only supported
media extensions and never shells out with filenames in command strings.
The model selector may not invent a replacement title absent from the request.
After listing, a matching bare name in the next turn is accepted. No persistent
file inventory, content interpretation, transliteration aliases or downloads.

Supported: MP3/WAV/FLAC/OGG/M4A/AAC/OPUS and MP4/MKV/WEBM/MOV/AVI/M4V.
Nonrecursive Desktop scope; paths outside Desktop and URLs are not accepted by
this player. Filename matching ignores case and punctuation; distinctive title
fragments are supported. Multiple matches ask a short ordinary question.
At most six names appear in the brief response; the tool returns the full list.

## Install / update

Copy `scripts/local-ovms/plugins/demo-media/` to the profile's `plugins/` and
`scripts/local-ovms/skills/local-media-player/` to `skills/media/`.
Enable `demo-media` in `plugins.enabled`; keep `general-voice` enabled.
The native loader orders demo-media first. Restart gateway/CLI normally after
backing up and updating these files; do not replace core or user configuration.
Ubuntu needs ffplay (ffmpeg package) and the desktop user's systemd manager.
Never copy generated demo assets over the user's real Desktop files.

Try "List media on Desktop", "Play Dragonfly", "Play nanshannan.mp3",
"Play the MP4 video", "播放视频", "Stop the video".
IM commands play on the Ubuntu host, not on the phone.

The owned transient unit is `hermes-demo-media.service`; it auto-exits at EOF.
Only this player is stopped/replaced, not VLC, browser or TTS processes.
Selection has a 20-second timeout without retries. Failures are reported, not
converted to success. Physical audibility and ASR/TTS overlap need manual checks.

## Tests

Run `scripts/run_tests.sh tests/skills/test_local_media_player_skill.py tests/skills/test_demo_media_plugin.py -q`.
Coverage includes listing, partial names, WAV audio, missing/ambiguous files,
symlink/path rejection, safe argv, tool completion and cross-task isolation.
Real local-model smoke should include list → title, exact/partial filename,
missing filename, ambiguous generic audio and stop, checking the owned player.
