---
name: local-media-player
description: Play local demo MP3 audio and MP4 video on Ubuntu.
version: 0.1.0
author: shawnxhong
platforms: [linux]
metadata:
  hermes:
    category: media
    tags: [audio, video, playback, mp3, mp4, offline]
---

# Local Media Player Skill

Play the fixed demo audio or video on this Ubuntu computer's desktop.
Playback is local and offline; do not download media, generate code, send
attachments, or claim you played something merely by describing it.

## When to Use

Use when asked to play demo audio/music/MP3 or demo video/MP4, in English
or Chinese: "Play the demo audio", "Play the video", "播放音频", "播放视频".
Also use to stop or check media previously launched through this skill.
Do not use for TTS answers, ASR recordings, travel, or unrelated questions.

## Prerequisites

Ubuntu desktop session with user systemd and ffplay (Ubuntu ffmpeg package).
The supplied helper is `scripts/play_media.py` inside this skill directory.
Default files are fixed, not chosen by the model:

- MP3: `~/hermes-demo-media/demo.mp3`
- MP4: `~/hermes-demo-media/demo.mp4`

Here `~` is the desktop user's home: `/home/agentdemo` on the development
host and `/home/intel` on Box. Do not use the development host's absolute
path after migration. No Internet, API key or cloud LLM is needed.

## How to Run

Use native `terminal` to execute `python3 <absolute-skill-directory>/scripts/play_media.py <action>`.
Resolve the skill directory from the path supplied when loading this skill;
do not invent a script or search the whole disk. If installed in the default
location it is `~/.hermes/skills/media/local-media-player`.
The helper returns promptly while the player continues independently.
Do not use terminal background jobs or wait for the entire clip to finish.

## Quick Reference

| Request | Action |
|---|---|
| Play audio, MP3, music / 播放音频、音乐 | `mp3` |
| Play video, MP4 / 播放视频 | `mp4` |
| Stop the demo media / 停止播放 | `stop` |
| Is it playing? / 是否正在播放 | `status` |

## Procedure

Every new play request requires a NEW `terminal` call, even if an earlier file
played successfully. Reading this skill is not playback. Never reuse an earlier
tool result to claim the newly requested MP3/MP4 started. Once loaded, reuse
these instructions instead of repeatedly loading the unchanged skill.

1. For an explicit audio/video request, run the corresponding action directly.
   Do not ask for a filename, permission or email address for these demo files.
   If the request gives no clue which medium, ask one short ordinary question.
2. Inspect returned JSON. `success: true`, `action: started`, `state: active`
   means the local player started, not that the clip finished or was heard.
3. Give one brief response in the user's language: "The demo video is playing."
   or "示例视频已开始播放。" No detailed report or automatic email.
4. On failure, state the actual issue briefly. Do not claim success, repeatedly
   retry, install packages, change audio routes/volumes, or use another file.
5. A new play request replaces only this skill's previous playback. Stop affects
   only its own player, never VLC, a browser, Hermes TTS, or other applications.
6. IM requests also play on the Ubuntu host; do not upload the file to IM unless
   separately asked. Use text responses, not local TTS, for IM.

Example: after stopping MP3, the user says "播放示例视频". Run a new terminal
command ending in `play_media.py mp4`. Only after that command reports success
and the file `demo.mp4`, reply "示例视频已开始播放。" If you cannot execute the
command or run out of tool budget, say you have not started the video.

## Pitfalls

- Video needs an active desktop, though the requesting terminal may be remote.
- This first version intentionally handles the two fixed files only.
- Playback may overlap a brief Hermes spoken acknowledgment; this skill does
  not change generic wake/ASR/TTS behavior or install audio coordination hooks.
- Do not raise global speaker volume or auto-open arbitrary URLs.
- "Stop" during other tasks is not automatically a media command: use context.

## Verification

Read the helper's result instead of guessing. `status` checks its owned player
process, not physical sound. If the user hears nothing despite an active
player, report that distinction and ask before changing their audio setup.
