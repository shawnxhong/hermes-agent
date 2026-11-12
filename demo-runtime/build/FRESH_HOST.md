# Build and run on a fresh Ubuntu development host

This release targets x86-64 Intel iGPU Ubuntu desktops with an existing working
PipeWire session, GPU kernel driver and TFN1/acpi_fan hardware interface. It does
not install kernel drivers, change audio routing or replace Dragonfly panel code.
The OS packages inside images are pinned independently of the host OS.

## Host prerequisites

Install Docker Engine with Compose v2 using your organization's supported Ubuntu
procedure. Use the containerd image store: these locks identify OCI manifests/
indexes, which differ from legacy Docker config IDs. After `docker load`, verify
every locked ID with `docker image inspect`; stop if any is unavailable rather
than substituting another image. The desktop user must be able to run `docker info` without sudo; log
out/in after changing groups. Install Git, Python 3.11+ (tar extraction filters),
python3-yaml, dpkg, patch, ffmpeg (ffplay), and pulseaudio-utils (pactl).
Run from that user's desktop session, with `systemctl --user`, `/dev/dri`,
`/dev/snd`, `/run/user/$UID/pulse/native`, DISPLAY and XAUTHORITY available.
A source build does not supply the host audio/GPU stack. Unsupported fan/device
layouts fail explicitly rather than guessing sysfs paths.

## External materials

Obtain these from the release maintainer through the authorized private asset
channel, preserving relative paths. A Git checkout alone is not sufficient.

| Group | Contents / authoritative lock | Destination used below |
|---|---|---|
| Offline dependencies | Ubuntu debs, CPython archive, Python wheels; `locks/os-build.json`, `python-build.json` | `/materials/dependencies/...` |
| Base images | Exact Ubuntu image IDs in `os-build.json`; load verified Docker archives | Docker image store |
| OVMS | Accepted 2026.2.1 image in `accepted-images.json`; load verified archive | Docker image store |
| AISP source | Authorized pause/resume-v2 SDK ZIP; `sdk-build.json` | `/inputs/aisp_sdk_src_pause_resume_v2.zip` |
| SDK dependencies | Sources/binaries checked by `sdk-build.json` | `/materials/dependencies/sdk-inputs/dependencies` |
| libpng | 1.6.43 ZIP; URL/hash in `sdk-build.json` | `/inputs/libpng-1.6.43.zip` |
| AEC inputs | Four files in `aec-reference/inputs.lock.json` | `/inputs/aec/` |
| Runtime binary/resources | GPU userspace, PortAudio, cues/KWS; `runtime-assets.json` | `/inputs/runtime-assets/` |
| Models | `locks/{llm,question-asr,wake-asr,tts}.json` | `/models/{llm,question-asr,wake-asr,tts}` |
| Lifecycle resources | `locks/lifecycle-audio.json` | `/resources/lifecycle-audio/` |
| Private settings | Profile binding JSON and per-channel `.env` | `/private/` (outside Git) |

Use `prepare_assembly.py verify-assets --manifest LOCK --directory DIR` for each
model/resource lock. Build stages hash-check their own inputs before use. Base
image IDs are local immutable identities, not registry URLs; do not assume they
can be downloaded by SHA. No historical Hermes product image is a build input.
Models come from pinned public upstreams. Authorized SDK/build material archives are distributed through private GitHub Release assets; see ONLINE_DEPLOY.md.

## Build

For the unified entrypoint, see [UNIFIED_BUILD.md](UNIFIED_BUILD.md). The manual
stage commands below remain available for debugging.

Clone `release/ubuntu-demo`, then check out the immutable release tag specified
by the maintainer (this release: `ubuntu-demo-20261111.2`). Keep the checkout
clean while building. Follow all commands in [SOURCE_BUILD.md](SOURCE_BUILD.md),
including explicit `--os-build` / `--toolchain-build` arguments. Keep outputs in
a new directory outside the checkout. All image build steps are offline after
materials have been provisioned. The final `check_candidates.py` receipt and
wake build receipt are used below.

## Provision without any old runtime

Create `/private` with mode 0700. Copy both profile binding examples from
`demo-runtime/profiles/` to `/private/state.bindings.json` and
`/private/gateway-state.bindings.json`; replace every REPLACE_ME value. Keep
those files at mode 0600. These provide private bindings only; product behavior
is defined by source templates. Optionally supply `/private/state/.env` and
`/private/gateway-state/.env` with authorized SMTP, Brave and IM credentials.
Do not copy historical sessions, mail queues, databases or pairing caches.
Service availability requires the corresponding credentials; no credential is
invented by the installer. The mail worker reads `platforms.email.home_channel.chat_id` from the voice
profile (the first private binding); `EMAIL_HOME_ADDRESS` in the existing
provider environment can override it. Keep those consistent with the legacy
recipient bindings. Existing provider environment keys are documented by
`plugins/platforms/email/adapter.py` and `gateway/config.py`; this release does
not add new behavior environment variables.

```sh
python3 demo-runtime/build/prepare_runtime.py \
  --private /private --output /runtime/hermes-release \
  --models /models --resources /resources \
  --checks /build/checks.json --wake-build /build/wake \
  --project hermes-release

docker compose -f /runtime/hermes-release/compose.yaml run --rm --no-deps -T cli init
docker compose -f /runtime/hermes-release/compose.yaml run --rm --no-deps -T gateway init
python3 /runtime/hermes-release/candidate_ctl.py check
python3 /runtime/hermes-release/candidate_ctl.py activate
python3 /runtime/hermes-release/candidate_ctl.py voice
```

The lifecycle command is currently named candidate_ctl.py even for fresh
installs. `activate` starts backgrounds and waits for running/healthy services;
`voice` opens the TUI. `status` inspects the stack. Close the TUI with Ctrl+C,
then `rollback` stops this fresh stack (there is no previous installation to
restore). It refuses to stop a mail worker with pending deliveries. On fresh
hosts, occupied service ports are rejected without stopping other applications.
Never run two stacks against the same microphone, ports or IM account.

This developer release uses explicit start/stop. It does not enable boot startup
or replace an existing `hermes-box-voice` command. Linked user helper units and
containers are not enabled for automatic login/reboot. Test voice wake, recording,
end phrase, scene switch, media playback/stop and configured delivery channels
on the target hardware before distributing it as a production deployment.

For public model downloads and private material downloads followed by unified provisioning, see [ONLINE_DEPLOY.md](ONLINE_DEPLOY.md).
