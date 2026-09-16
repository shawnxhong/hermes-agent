# Laptop runtime asset convergence — candidate

This task is source preparation, not a release or deployment. Base:
`e0fe3156cf6d86658f5b61702d86c25b9571e0ab`. Preserve the integrated core,
English-only interaction, Hello Intel aliases, offline degradation and scene
switching. Do not reapply the legacy runtime's already-integrated patches.

## Local TTS adapter

`scripts/local-ovms/kokoro_tts.py` is derived from the installed laptop adapter.
It retains the English Kokoro `af_maple` / `en-us` path, speed and output format.
The existing Kokoro v1.1 model bundle keeps its historical `zh` filenames; this
does not make English output Chinese. The mixed-language helper is preserved
for compatibility, not enabled as a new product language. Optional Chinese
frontend imports are deferred so English does not initialize that frontend.

The command now requires `--model-dir` rather than assuming a user home path.
Provide `--input`, `--output`, `--model-dir`, and optionally `--english-voice`
and `--speed`. It writes WAV only; it never plays audio.

`requirements-kokoro.lock` records installed exact dependencies including a
commit-pinned Kokoro source. It is a provenance snapshot, not proof of a fresh
rebuild: wheel hashes, offline wheel provisioning, native libraries and a
clean-environment rebuild remain release validation work. Do not install these
dependencies into a currently running Hermes environment as part of review.

## Fleet ownership

Private `hermes-demo-ops` owns deployment units, host overlays and launcher
installation. The PTY supervisor and scene logic stay product code here.
Existing `scripts/local-ovms/*.service` are legacy compatibility examples, not
the authoritative fleet units. Do not install them over the ops templates.
Existing `hermes-mode` and `hermes-box-scene` continue to be referenced by the
ops asset manifest from this exact app release, not hand-copied runtime code.
No old path is removed until all callers are switched as a reviewed release.

## Approved skill disposition

Deploy the existing remote travel-concierge, local-media-player (including
its helper), and demo-media plugin as matched components. No skill text is
rewritten in this task. The legacy hardware-shopping-comparison skill and
its pricing reference are excluded and explicitly retired by the ops manifest.
The actual removal from active runtime discovery is deferred to deployment.

Do not overwrite sessions, memory, outbox, models, media or credentials.
The box_a runtime input remains outstanding before a common fleet baseline.
