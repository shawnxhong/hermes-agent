---
name: local-media-player
description: List and play Desktop audio and video by name.
version: 0.4.0
author: shawnxhong
platforms: [linux]
metadata:
  hermes:
    category: media
    tags: [audio, video, playback, mp3, mp4, offline]
---

# Local Media Player Skill

List real media files on this Ubuntu user's Desktop and play the requested
item with `local_media`. No fixed demo filenames, downloads or GUI automation.

## When to Use

Use for "list the media on Desktop", "ls Desktop", "play Dragonfly",
"play nanshannan.mp3", "play the MP4 video", "put on some music",
"桌面上有哪些视频", "播放 Dragonfly" and similar English/Chinese commands.
An explanation such as "What is MP4?" is not a play request.

## Prerequisites

The enabled `demo-media` native plugin, its installed helper, ffplay and an
active Ubuntu graphical user session. Files reside directly in `~/Desktop`.
Supported audio: MP3, WAV, FLAC, OGG, M4A, AAC, OPUS.
Supported video: MP4, MKV, WEBM, MOV, AVI, M4V.
Hidden files, empty files, subfolders and symlinks are excluded.
No Internet or cloud LLM is needed. Do not assume SSH means no desktop exists.

## How to Run

Use `local_media`; its helper `scripts/play_media.py` owns listing, matching,
playback and startup verification. Do not build another player or use GUI
automation. If the native tool is deferred, use normal tool discovery.

## Quick Reference

| Request | action | target |
|---|---|---|
| List Desktop media | `list` | empty |
| Play a named file/title | `play` | user's filename or name fragment |
| Generic audio/music | `mp3` | empty |
| Generic video | `mp4` | empty |
| Stop this player | `stop` | empty |
| Player state | `status` | empty |

## Procedure

1. For a list request, call `list` and name only the returned files. For a
   play request, call the corresponding action; the helper scans Desktop
   freshly before matching, so a separate listing call is not required.
2. Copy the requested name into `target`. Full names or distinctive fragments
   work; case, spaces and punctuation are normalized. Never substitute a
   different title, translate a title, invent a path or assume demo files exist.
3. An unspecified medium plays directly only when exactly one matching file
   exists. Multiple matches return choices: ask one short ordinary question,
   then accept the filename/title in the next reply. Do not use `clarify`.
4. A missing match starts nothing. Briefly report that; do not retry, download,
   inspect the desktop or silently choose another file.
5. Successful playback is the completion boundary. Return one brief reply in
   the user's language. Never call `computer_use`, take screenshots, wait for
   the clip to end or launch a second verification loop.
6. Every fresh play request needs fresh execution. Stop affects only the
   plugin-owned player, not VLC, a browser or Hermes TTS.
7. IM commands play on this Ubuntu computer and receive an IM response, not
   media streamed to the phone. Do not email playback results automatically.

## Pitfalls

- Match names, not presumed file contents; semantic descriptions of unseen
  video content are not evidence. Ambiguous names require user selection.
- Unrelated tasks and combined multi-task requests retain native behavior.
- An isolated stop only applies immediately after a media task in this process
  and session. File choices are not global memory or permanent preferences.
- No global device, volume, wake-word or ASR/TTS changes. Voice acknowledgment
  may overlap media audio; physical acoustic acceptance remains a manual check.

## Verification

Use only the current tool result. The native media workflow completes after
one local action-selection call and helper execution. Process startup does not
prove physical audibility or completion of the whole file.
