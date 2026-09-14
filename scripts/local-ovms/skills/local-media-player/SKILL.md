---
name: local-media-player
description: Play MP3/music/audio or MP4/video locally; stop playback.
version: 0.3.0
author: shawnxhong
platforms: [linux]
metadata:
  hermes:
    category: media
    tags: [audio, video, playback, mp3, mp4, offline]
---

# Local Media Player Skill

Play local audio/music/MP3 or video/MP4 using the native `local_media` tool.
The enabled `demo-media` plugin completes ordinary media commands directly
after verified execution, without a second model-driven desktop check.

## When to Use

Use playback intent, not exact wording or the word "demo": "play the mp4
video", "play a video", "open the video", "MP four video", "put on some
music", "play the MP3", "播放视频", "打开MP4", "放一段音乐".
Questions like "What is MP4?" are explanations, not playback requests.
Do not silently replace a named different movie/song/file or URL with a demo.
Unrelated tasks retain ordinary Hermes behavior.

## Prerequisites

The native `demo-media` plugin and installed local player helper are required.
The helper determines readiness; do not inspect windows, screenshots, displays,
packages or permissions beforehand. SSH does not imply there is no desktop.

Fixed defaults on the Ubuntu desktop user's account:

- MP3: `~/hermes-demo-media/demo.mp3`
- MP4: `~/hermes-demo-media/demo.mp4`

No Internet or cloud LLM is required. `~` is the current desktop user's home,
not a hardcoded development-host path. Operator setup provides ffplay/systemd.

## How to Run

Call `local_media` with the appropriate `action`. If deferred, discover the
native local_media toolset using normal tool discovery. Do not substitute
desktop automation, shell commands, Python execution, browser or TTS tools.
If unavailable, report the missing plugin; do not build another player.

## Quick Reference

| Request | action |
|---|---|
| MP4, video, MP four, 视频 | `mp4` |
| MP3, audio, music, MP three, 音频、音乐 | `mp3` |
| Stop this media / 停止播放 | `stop` |
| Is the player running? / 播放状态 | `status` |

## Procedure

1. No filename is needed for generic requests: use the fixed default medium.
2. Every new play request needs a new tool execution; previous success is not
   evidence for this request. Do not ask for an upload, email or permission
   for these fixed demo files.
3. Tool success is the completion boundary. `workflow_complete: true` means
   the operation is done. Return ONE short confirmation in the user's language.
   Do not call `computer_use`, take screenshots or verify the desktop afterward.
4. If the player reports failure, briefly say so. No automatic retry, invented
   repair, package install, device/volume change or alternative playback route.
5. "Started" does not mean "finished" or "heard". The tool verifies the
   process, not physical audibility. Playback ends automatically with the file.
6. Stop affects only this plugin's player, never VLC, a browser or Hermes TTS.
7. IM commands also play on this Ubuntu computer; reply in IM without local
   TTS or automatic email. Voice uses the existing short reply path.

## Pitfalls

- A named different file or combined multi-task request is not a default-media
  shortcut. Handle other tasks normally; do not discard part of the request.
- An unrelated next turn must not inherit media restrictions. An isolated
  "stop" applies only when the immediately preceding task was media.
- A brief voice acknowledgment may overlap the clip; no global audio rules
  or wake/ASR behavior are changed by this skill.

## Verification

Use only the current tool result. The plugin's normal media workflow ends the
turn after one local action-selection call and player execution, preventing
post-playback screenshot retry loops. Physical audio remains a human check.
