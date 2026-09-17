---
name: local-media-player
description: List, play or stop local Desktop audio, music and video, including MP3 and MP4.
---

# Local media

Use only `local_media` for these requests. Files are in the user's Desktop.
Do not invent demo filenames, download files or control the desktop visually.

Use `action=list` when the user asks what is available or you need the exact
filename. Play with `action=play` and the requested title or filename as
`target`. For an unspecified audio or video request, use `action=mp3` or
`action=mp4`. If several files match, ask one short ordinary question; if none
match, say nothing was started. Do not guess or retry repeatedly.

Use `action=stop` to stop the managed player. Use `action=status` only if the
user asks whether it is running. Never kill unrelated applications.

After a successful start or stop, give one short English confirmation and
finish. Do not wait for media to end, inspect screenshots, call computer use,
or loop on status. Startup confirms the process, not physical audibility.

Do not email automatically. Questions such as "What is MP4?" are ordinary
knowledge questions, not playback requests.
