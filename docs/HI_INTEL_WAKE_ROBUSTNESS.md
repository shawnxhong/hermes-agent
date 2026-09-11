# Hi Intel Wake-Word Robustness Plan

Updated: 2026-09-11

## Goal

Keep the public wake phrase **“Hi Intel”** while improving recognition across
different US-English speakers. The balanced acceptance target is:

- at least 90% positive recall across the available speakers;
- no more than one false wake in eight hours of representative negative audio;
- callback dispatch within 500 ms of the detector result;
- no change to ASR, TTS, one-shot push-to-talk, follow-up capture, model, or
  messaging behavior.

The target cannot be signed off from synthetic speech alone. It requires a
small, consented local corpus from at least two or three speakers plus ambient
negative audio from the demo room.

## Implementation

1. Keep `wake_word.phrase: Hi Intel` as the only user-visible phrase.
2. Let the local Sherpa engine enroll hidden phonetic variants. Every variant
   canonicalizes to `hi intel`, so routing, UI state, logs, and callbacks never
   expose a different wake phrase.
3. Add optional Sherpa controls under `wake_word.sherpa`:
   `aliases`, `keywords_threshold`, `keywords_score`, `max_active_paths`, and
   `num_trailing_blanks`. If the fields are absent, the engine retains its
   previous threshold mapping and Sherpa defaults.
4. Ship a developer-only local recorder and replay evaluator at
   `scripts/local-ovms/check_wake_word_recall.py`. Recordings remain under the
   selected private corpus directory and are never added to Git.
5. Select parameters from replay results, not intuition. A candidate is
   accepted only when both the recall and false-wake targets have enough input
   evidence. Short negative recordings are reported as insufficient evidence.
6. Do not add automatic gain control initially. The evaluator reports loudness
   quartiles and stereo imbalance. Add bounded normalization only if the
   low-volume recall gap is at least ten percentage points, and investigate
   channel selection if imbalance exceeds 12 dB.
7. If this Sherpa path cannot meet the acceptance target, evaluate a custom
   local openWakeWord ONNX model as a later, isolated change. Do not introduce
   a cloud wake service, an API-key dependency, or always-on Whisper.

## Initial configuration

The voice launcher enrolls these hidden variants for the active `Hi Intel`
profile:

```yaml
wake_word:
  phrase: Hi Intel
  sherpa:
    aliases:
      - High Intel
      - Hi in tell
      - Hi indel
```

These variants cover common acoustic/tokenization confusions without teaching
users a second phrase. The current effective threshold remains unchanged until
real replay data supports a different value.

## Calibration workflow

Run from the canonical checkout with the installed Hermes voice environment:

```bash
python scripts/local-ovms/check_wake_word_recall.py record-positive \
  --speaker speaker-1 --count 10 --consent
python scripts/local-ovms/check_wake_word_recall.py record-negative \
  --label demo-room --minutes 30 --consent
python scripts/local-ovms/check_wake_word_recall.py evaluate
```

Repeat positive recording for each speaker. Negative audio must not contain an
intentional wake phrase. Thirty minutes is useful for an initial smoke check;
accumulate at least eight representative hours before final acceptance. The
evaluator prints JSON containing per-speaker
recall, aggregate recall, false wakes per hour, latency, level diagnostics, and
whether the evidence is sufficient. Its parameter grid can be overridden with
`--thresholds`, `--scores`, `--active-paths`, and `--trailing-blanks`.

Recording `record-positive` again with the same `--speaker` replaces that
speaker's earlier batch. Replacement happens only after every new sample is
recorded successfully; cancellation or microphone failure keeps the previous
batch intact. Other speakers and all negative recordings are unchanged.

Raw audio can be removed after review with the explicit command:

```bash
python scripts/local-ovms/check_wake_word_recall.py purge --confirm-delete
```

## Release and rollback

Deploy only the reviewed files after unit and replay checks. Do not restart an
active demo session. A new Hermes process picks up the engine and launcher
changes. Rollback consists of restoring `tools/wake_word.py`,
`hermes_cli/config_defaults.py`, and `hermes-mode`, then starting a fresh CLI.
