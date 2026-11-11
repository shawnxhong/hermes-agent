# One-command Ubuntu source build

From a clean checkout of the release source:

```sh
./scripts/build-ubuntu-demo.sh --assets /srv/hermes-build-assets/RELEASE \
  --output "$HOME/hermes-builds/RELEASE"
```

No sudo, models, private service credentials, old runtime, audio session or panel
are needed. Requires Ubuntu x86-64, Git, Python 3.11+ with PyYAML, Docker Engine
with the containerd image store and the legacy builder available, dpkg-deb and
patch. The user needs Docker access. Allow at least 30 GiB free disk (more if
retaining several builds). Build outputs and source must be separate directories.

External asset layout (all hashes come from version-controlled locks):

```
ASSETS/
  dependencies/       # offline debs, CPython, wheels, sdk-inputs/dependencies
  runtime-assets/     # locked GPU/PortAudio/cue/KWS assets
  aec/                # the four locked AEC source/header/deb files
  sdk.zip             # authorized AISP source archive
  libpng.zip          # pinned libpng source archive
  images.tar          # locked Ubuntu bases and OVMS OCI archive (if not loaded)
```

The maintainer supplies these through the authorized asset channel. No old
Hermes product image is used. Assets can be root-owned and read-only. No online
package resolution is performed during builds. `images.tar` is only needed if
locked external image IDs are absent; the script validates its OCI index before
loading and checks availability afterward. Missing material paths are reported
together before any compilation.

Options:

- `--check`: validate host, materials and image/archive availability, then exit.
- `--jobs 2 --memory-gb 8`: default limits. Native compile runs in a bounded
  container; Docker build RUN containers receive CPU quota and memory/swap
  limits. The legacy builder is selected explicitly so those options are not
  silently ignored by BuildKit. Archive copying and Docker layer export are
  daemon/host work outside compilation cgroups; avoid concurrent heavy workloads.
- `--no-cache`: disable Docker build layer reuse. Does not purge shared images.
- `--gpu auto|required|off`: default auto checks GPU enumeration if `/dev/dri`
  exists. GPU permission failure is an error, not a silent pass. No model inference.
- `--resume`: reuse only completed stages with matching source/input/policy
  fingerprints, verified receipts/native artifacts and available image IDs.
  Changed inputs conservatively invalidate dependent stages; failed stages get
  a new attempt directory. Logs and failed outputs are retained.

The runner executes OS builds, Python environments, SDK/bridge, four application
roles and wake/AEC, then dependency/import/GPU checks and fresh CLI/Gateway init
with dummy private bindings and networking disabled. Runtime services are never
started. It does not change production tags, run docker prune, modify audio,
copy history, or send mail/IM. `release.json` contains final immutable image IDs
and source/input provenance; `build-state.json` and stage logs support diagnosis.

Use Ctrl+C to stop. The runner signals its subprocess group and retains state;
inspect any outstanding Docker daemon build before resuming. A reused stage is
reported as REUSE. Sharing a Docker daemon means a new Linux user is not a
security boundary, and Docker group membership grants privileged host access.

After successful build, follow FRESH_HOST.md for a separately authorized runtime
installation. A successful source-build smoke is not a microphone/TTS acceptance.
