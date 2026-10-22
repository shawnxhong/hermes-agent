---
name: local-media-player
description: Play Desktop music/video. Every stop or close request requires a fresh local_media stop call, even after an earlier stop; voice and IM share the device.
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

For "stop music", "close the video", "stop playback", or a contextual "stop",
call `local_media(action="stop")` once, without a filename or a prior list/status
call. It stops all current demo media and current/queued TTS on this device.
Call stop for EVERY new stop request, even if you previously said "already
stopped". Voice and IM share this device; another channel may have restarted
playback. Never infer current playback from conversation history. The tool
repeats cleanup even when already stopped and cancels older queued playback.
Only report this invocation's result. On failure report it briefly; do not retry or use
terminal/computer tools. A new short confirmation is allowed after stopping.
Use `action=status` only if the
user asks whether it is running. Never kill unrelated applications.

After a successful start or stop, give one short English confirmation and
finish. Do not wait for media to end, inspect screenshots, call computer use,
or loop on status. Startup confirms the process, not physical audibility.

Do not email automatically. Questions such as "What is MP4?" are ordinary
knowledge questions, not playback requests.
