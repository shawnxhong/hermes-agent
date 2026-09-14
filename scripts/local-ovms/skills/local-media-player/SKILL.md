---
name: local-media-player
description: Play MP3/music/audio or MP4/video locally; stop playback.
version: 0.2.0
author: shawnxhong
platforms: [linux]
metadata:
  hermes:
    category: media
    tags: [audio, video, playback, mp3, mp4, offline]
---

# Local Media Player Skill

Handle requests to play audio/music/MP3 or video/MP4 on this Ubuntu desktop.
When no specific file is named, use the fixed default file for that medium.
Playback is local and offline; do not download media, generate code, send
attachments, or claim you played something merely by describing it.

For playback, the next action is a `terminal` call to the supplied Python
helper. Do not use `computer_use`, browser clicks, `execute_code`, or
`text_to_speech` to play these files. This is a command-line operation even
though the video appears on the desktop. Run the helper with `python3`, not
as an executable; no chmod or source-code inspection is needed.

**Execute first, diagnose only a returned error.** The helper checks its own
requirements. Do not check desktop windows, displays, packages or permissions
before running it. A terminal/SSH session does NOT imply that playback is
unavailable: the helper launches into the user's existing desktop session.
Never infer "headless" from a failed GUI tool. The tested command for MP4 is:

```bash
python3 ~/.hermes/skills/media/local-media-player/scripts/play_media.py mp4
```

For MP3, change only the last argument to `mp3`. Send this as the `command`
argument of native `terminal`, not as Python code. Use the skill directory
reported by the loader instead of the default path if using another profile.

## When to Use

Use based on playback intent, not an exact phrase or the word "demo".
"Play the mp4 video", "play an MP4", "play a video", "open the video",
"show me the video", "put on some music", "play the MP3", "播放视频",
"打开 MP4", and "放一段音乐" all belong here.
Speech transcripts may render MP4 as "MP four" / "M P 4" and MP3 as
"MP three" / "M P 3"; interpret these by context as the same media formats.
Politeness, capitalization and words such as "the", "a", "local", "file",
"sample" or "demo" do not change the intent. These are examples, not a whitelist.
Also use to stop or check media previously launched through this skill.
Do not use for TTS answers, ASR recordings, travel, or unrelated questions.
Questions such as "What is MP4?" ask for an explanation, not playback.
If a particular movie/song, another filename, or a URL is requested, do not
silently substitute the demo: explain that this skill currently supports the
fixed local files and ask whether to play the default instead.

## Prerequisites

The operator has installed ffplay and configured the Ubuntu user service.
Let the helper determine runtime readiness; no preliminary checks are needed.
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
   MP4/video selects `mp4`; MP3/audio/music selects `mp3`. An unspecified
   filename is NOT missing information: use the fixed default for that medium.
   Do not ask the user to upload a file, give its path, or say "demo".
   Do not search the web/disk, open a browser, or explain how to play files
   instead of running the supplied helper. Do not ask for another permission
   or email address for these default files.
   If the request gives no clue which medium, ask one short ordinary question.
2. Inspect returned JSON. `success: true`, `action: started`, `state: active`
   means the local player started, not that the clip finished or was heard.
3. Give one brief response in the user's language: "The demo video is playing."
   or "示例视频已开始播放。" No detailed report or automatic email.
   Mention an error or repair only if this request's tool result reports it;
   do not invent a permission issue before announcing success.
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

Example: "Could you please play the mp4 video?" is already a complete
request. Execute `python3 <this-skill-directory>/scripts/play_media.py mp4`,
check the current result, then briefly confirm. Do not ask "Which video?".

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
