# Download, build and provision a new Ubuntu Intel desktop

Product behavior is pinned by the source release. Model files are downloaded
from public upstreams at fixed revisions, not copied from a previous device.
AISP SDK source and offline build materials are separate **private GitHub
Release assets** in `shawnxhong/hermes-demo-ops`. Access to that repository is
required. No service credentials or previous sessions are in those assets.

## Host preparation

Target: Ubuntu x86-64, Python 3.11+, Intel GPU with working kernel driver,
PipeWire desktop session, and the baseline TFN1/acpi_fan hardware interface.
Unsupported fan hardware is reported; the installer does not guess or alter
firmware, drivers, audio routing, panel scripts or existing production commands.
Allow at least 80 GiB free disk for models, source materials and intermediate
build layers; more if retaining multiple builds.

Clone the release repository/tag, then on a **fresh host without Docker**:

```sh
sudo ./scripts/bootstrap-ubuntu-demo.sh "$USER"
# Log out and back in, then:
./scripts/bootstrap-ubuntu-demo.sh --check
```

The bootstrap follows Docker's [Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/)
and [containerd image-store](https://docs.docker.com/engine/storage/containerd/)
configuration. It refuses to replace an existing Docker installation. On an
existing development machine use `--check` and resolve missing host prerequisites
without changing a running deployment. Docker group access is privileged.

## Obtain materials from GitHub

Use a GitHub credential with read access to the private operations repository:

```sh
./scripts/download-ubuntu-demo-materials.sh \
  --output "$HOME/hermes-install/materials" --git-credential
```

Alternatively provide `GH_TOKEN` or `GITHUB_TOKEN` through your credential manager.
Tokens are not written into runtime files or command arguments. Asset download
redirects strip authorization when changing hosts. Every archive is hash-checked
against the version-controlled manifest before safe extraction. Model weights
are excluded from the private bundle; legacy KWS is downloaded separately too.

## Configure private bindings

Create a private directory (0700); copy both
`demo-runtime/profiles/*.bindings.example.json` there as
`state.bindings.json` and `gateway-state.bindings.json` (0600), replacing all
`REPLACE_ME` values. The custom local OVMS API key can be `local`; Piper's unused
voices directory can be `/models/tts`. Set the three recipient fields to your
intended email recipients. Optional `state/.env` and `gateway-state/.env` provide
SMTP, Brave and IM credentials. No cloud LLM credential is required.

Gateway is not started by default on a new installation. Add `--enable-gateway`
when valid IM configuration is present. Mail/search/IM need their respective
credentials; installing their tools does not create accounts or authorize sends.

## Build and provision

```sh
./scripts/deploy-ubuntu-demo.sh \
  --assets "$HOME/hermes-install/materials" \
  --private "$HOME/hermes-install/private" \
  --prefix "$HOME/hermes-install/release" \
  --project hermes-local --install-commands
```

Omit `--assets` to have the same deployment command download the private assets
into `PREFIX/materials` first (use `--git-credential` or a token environment variable).

This downloads approximately 21.7 GB of pinned LLM, question ASR, wake ASR and
TTS artifacts, normalizes two small product configuration files, checks every
file, builds SDK/bridge/AEC/Python/application roles, runs image and profile
initialization checks, then prepares the runtime and installs user commands.
No old Hermes image or runtime is used. Ubuntu/OVMS upstream binary images and
pinned GPU/PortAudio binaries remain intentional external dependencies; this is
not a claim that every third-party component is compiled from source.

Repeated invocation resumes verified downloads and completed compatible build
stages. Existing mismatched files, runtime source or foreign command wrappers
are refused. Interrupted preparation without a provenance receipt requires a
new prefix. No services start during installation. The offline prepared check
allows the user desktop audio sockets to be absent; actual startup checks them.

After logging in to the target user's desktop:

```sh
~/.local/bin/hermes-box-voice start
hermes-box-voice stop
hermes-box-scene switch travel
hermes-box-runtime status
hermes-box-runtime shutdown
```

Ensure `~/.local/bin` is on PATH (Ubuntu adds it on login). `voice stop` stops
the interactive CLI; backgrounds remain available. `runtime shutdown` stops
the whole installation after mail delivery drains. Boot autostart is not
enabled by this developer installer. On subsequent boots, `voice start` starts
this same stack again. No other user's runtime is switched.

Inspect `release/build/release.json`, `release/runtime/install-provenance.json`,
`release/models/download-receipt.json` and the stage logs for provenance.
Microphone, wake/end phrase, TTS, media, and configured delivery channels still
require target-hardware acceptance. A second user on the production laptop is
used for isolated download/build/provision tests, not concurrent microphone use.
