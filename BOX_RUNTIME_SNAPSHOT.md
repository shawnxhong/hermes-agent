# Engineering Box runtime source snapshot

Snapshot date: 2026-09-14. This branch preserves the Git-visible files from
the deployed Hermes runtime, based on upstream d63f996a7. It is intentionally
separate from the ongoing local-ovms-voice development branch: the runtime
contains an older upstream base with selectively deployed integrations.

Includes the shared answer-window That's all endpoint and configuration-driven
silence timeout. At capture time the Box used a four-second silence fallback.
The runtime's missing flake.lock and uv.lock are represented as deletions,
not silently replaced with different development versions.

This is a source snapshot, not a complete machine backup. Ignored files,
credentials, host configuration, models, recordings, session data and external
user-installed plugins/skills are not included. Existing bundled test/audio
assets tracked upstream remain part of the source tree.

No service restart or live checkout reset was performed for this snapshot.
The latest answer endpoint was previously verified with 59 focused regressions
and a Box-local noisy PCM / keyword / Whisper replay. This snapshot as a whole
has not undergone a full test-suite run.
