---
name: local-media-player
description: List and play Desktop audio and video by name.
version: 0.5.0
author: shawnxhong
platforms: [linux]
metadata:
  hermes:
    category: media
    tags: [audio, video, playback, mp3, mp4, offline]
---

# Local Media Player

Use English for requests to list or play real media files directly under
`~/Desktop`. Use the native `local_media` tool; do not use GUI automation,
downloads or presumed demo filenames. Explanations such as "What is MP4?" and
unrelated or combined tasks stay with normal Hermes.

Actions: `list`, `play` with the user's filename/title fragment, generic `mp3` or
`mp4`, `stop`, and `status`. Supported audio is MP3, WAV, FLAC, OGG, M4A, AAC and
OPUS; supported video is MP4, MKV, WEBM, MOV, AVI and M4V. Hidden, empty, nested
and symlinked files are excluded.

List from the current tool result. For playback, copy the requested name without
translation or substitution. Multiple matches require one short ordinary question;
a missing match starts nothing and is not retried. Successful process startup ends
the workflow: respond briefly without screenshots, waiting for playback to finish,
or a second verification loop. Stop only affects the plugin-owned player. Do not
email playback results automatically. Process startup does not prove physical
audibility.
