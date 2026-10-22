---
name: local-media-player
description: List, play or stop local Desktop audio, music and video, including MP3 and MP4.
---

# Local media

Use only `local_media` for these requests. Files are in the user's Desktop.
Do not invent demo filenames, download files or control the desktop visually.

For a named playback request, call `local_media(action="play")` once with the
user's title or filename fragment as `target`. The tool selects the closest
filename match automatically, including when several files are similar.
Briefly confirm the actual filename returned by the tool. If no match is
found, say nothing was started; do not retry or invent a filename.
Use `action=list` when the user asks what is available. For an unspecified
audio or video request, use `action=mp3` or `action=mp4`; if the tool returns
multiple choices because no title was provided, ask one short question.

Use `action=stop` to stop the managed player. Use `action=status` only if the
user asks whether it is running. Never kill unrelated applications.

After a successful start or stop, give one short English confirmation and
finish. Do not wait for media to end, inspect screenshots, call computer use,
or loop on status. Startup confirms the process, not physical audibility.

Do not email automatically. Questions such as "What is MP4?" are ordinary
knowledge questions, not playback requests.
