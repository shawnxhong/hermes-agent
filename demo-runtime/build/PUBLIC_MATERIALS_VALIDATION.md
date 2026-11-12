# Public installer materials validation — 2026-11-12

This release removes the private GitHub repository requirement for a fresh
source installation. AISP SDK is Apache-2.0 at its author's direction.

Validation:

- 36 build/download/provisioning tests passed, including anonymous URL selection,
  optional authentication, endpoint validation, hash failures and safe extraction.
- Newly licensed SDK compiled offline with the pinned toolchain: all four stages
  passed (configure, native build, install, Hermes wake bridge build).
- The experimental `hermes-build-test` user downloaded the entire new SDK archive
  without GH_TOKEN/GITHUB_TOKEN or a Git credential helper. SHA-256 verification
  and safe extraction passed.
- All 33 third_party entries are byte-identical to the previous SDK archive.
- The ten unchanged material archives retain their previous hashes. Models and
  application behavior are unchanged; previous full source-build validation
  remains applicable. This run did not repeat microphone/TTS hardware acceptance.
- Production containers, runtime selection and service credentials were not changed.

Source installer: `ubuntu-demo-20261112.2`.
Public materials: `shawnxhong/hermes-agent`, release
`ubuntu-demo-materials-20261112.2`. The version-controlled materials manifest
records every asset URL, size and checksum. Previous release `.1` remains intact.
