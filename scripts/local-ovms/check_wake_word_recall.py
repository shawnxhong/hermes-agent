#!/usr/bin/env python3
"""Record and replay a private, local wake-word acceptance corpus.

Audio never leaves this machine. The default corpus is deliberately outside
the source checkout, under HERMES_HOME/private. Recording requires explicit
consent, and deletion requires a marker plus an explicit confirmation flag.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import wave
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MARKER = ".hermes-wake-corpus"
MIN_POSITIVE_SPEAKERS = 2
MIN_POSITIVES_PER_SPEAKER = 5
MIN_NEGATIVE_HOURS = 8.0
TARGET_RECALL = 0.90
MAX_FALSE_WAKES_PER_HOUR = 0.125
MAX_PROCESSING_MS = 500.0


def _default_corpus() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "private" / "wake-word-calibration"


def _safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.")
    if not label:
        raise ValueError("label must contain at least one letter or digit")
    return label[:64]


def _safe_corpus_root(root: Path) -> Path:
    root = root.expanduser().resolve()
    protected_ancestors = {REPO_ROOT.resolve(), *REPO_ROOT.resolve().parents}
    if (
        root == Path(root.anchor)
        or root == Path.home().resolve()
        or root in protected_ancestors
        or (root / ".git").exists()
        or "wake" not in root.name.casefold()
    ):
        raise ValueError(
            "refusing unsafe corpus path; use a dedicated directory whose name contains 'wake': "
            f"{root}"
        )
    return root


def _prepare_corpus(root: Path) -> Path:
    root = _safe_corpus_root(root)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    marker = root / MARKER
    if not marker.exists():
        marker.write_text(
            "Private local wake-word recordings. Do not commit or upload.\n",
            encoding="utf-8",
        )
        os.chmod(marker, 0o600)
    return root


def _require_corpus_marker(root: Path) -> Path:
    root = _safe_corpus_root(root)
    if not (root / MARKER).is_file():
        raise ValueError(f"not a Hermes wake corpus (missing {MARKER}): {root}")
    return root


def _write_wav(path: Path, audio, sample_rate: int, channels: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(audio.tobytes())
    os.chmod(path, 0o600)


def _record_clip(path: Path, seconds: float, device: int | str | None, channels: int) -> None:
    import numpy as np
    import sounddevice as sd

    from tools.wake_word import _capture_sample_rate, _describe_input_device

    details = _describe_input_device(sd, device)
    sample_rate = _capture_sample_rate(details)
    frames = max(1, int(round(seconds * sample_rate)))
    stream = sd.InputStream(
        device=device,
        samplerate=sample_rate,
        channels=channels,
        dtype="int16",
        blocksize=0,
    )
    chunks = []
    remaining = frames
    stream.start()
    try:
        while remaining:
            block, _overflowed = stream.read(min(remaining, sample_rate))
            chunks.append(np.asarray(block, dtype=np.int16).copy())
            remaining -= len(block)
    finally:
        stream.stop()
        stream.close()
    _write_wav(path, np.concatenate(chunks, axis=0), sample_rate, channels)


def _configured_device(cfg: dict[str, Any], override: str | None) -> int | str | None:
    if override is not None:
        stripped = override.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
        return stripped or None
    from tools.wake_word import _input_device

    return _input_device(cfg)


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _validate_positive_destination(destination: Path) -> None:
    if destination.is_symlink():
        raise ValueError(
            f"refusing to replace symlinked speaker directory: {destination}"
        )
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"speaker destination is not a directory: {destination}")


def _replace_positive_batch(staging: Path, destination: Path) -> None:
    """Install a completed speaker batch, restoring the old one on failure."""
    _validate_positive_destination(destination)
    previous = destination.with_name(
        f".{destination.name}.previous-{os.getpid()}-{time.time_ns()}"
    )
    had_previous = destination.exists()
    if had_previous:
        os.replace(destination, previous)
    try:
        os.replace(staging, destination)
    except BaseException:
        if had_previous and previous.exists() and not destination.exists():
            os.replace(previous, destination)
        raise
    if had_previous:
        shutil.rmtree(previous)


def _record_positive(args: argparse.Namespace) -> int:
    if not args.consent:
        raise ValueError("recording requires --consent from every recorded speaker")
    from tools.wake_word import load_wake_word_config

    cfg = load_wake_word_config()
    root = _prepare_corpus(args.corpus)
    speaker = _safe_label(args.speaker)
    device = _configured_device(cfg, args.device)
    destination = root / "positive" / speaker
    _validate_positive_destination(destination)
    positive_root = destination.parent
    positive_root.mkdir(parents=True, exist_ok=True)
    os.chmod(positive_root, 0o700)
    previous_count = len(list(destination.glob("*.wav"))) if destination.is_dir() else 0
    staging = Path(
        tempfile.mkdtemp(prefix=f".{speaker}-recording-", dir=str(positive_root))
    )
    os.chmod(staging, 0o700)
    try:
        for index in range(1, args.count + 1):
            if not args.automatic:
                input(
                    f"Sample {index}/{args.count}: press Enter, then say the wake phrase once... "
                )
            else:
                print(f"Sample {index}/{args.count}: recording in one second...", flush=True)
                time.sleep(1)
            path = staging / f"{_timestamp()}-{index:03d}.wav"
            _record_clip(path, args.seconds, device, args.channels)
            print(f"captured sample {index}/{args.count}")
        _replace_positive_batch(staging, destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    print(
        f"installed {args.count} samples for {speaker}; "
        f"replaced {previous_count} previous samples"
    )
    return 0


def _record_negative(args: argparse.Namespace) -> int:
    if not args.consent:
        raise ValueError("recording requires --consent from everyone who may be recorded")
    from tools.wake_word import load_wake_word_config

    cfg = load_wake_word_config()
    root = _prepare_corpus(args.corpus)
    label = _safe_label(args.label)
    device = _configured_device(cfg, args.device)
    destination = root / "negative" / label
    total_seconds = args.minutes * 60.0
    clip_seconds = min(args.clip_seconds, total_seconds)
    clip_count = max(1, math.ceil(total_seconds / clip_seconds))
    print(
        "Record representative ambient speech/noise without an intentional wake phrase. "
        "Press Ctrl+C to stop; WAVs completed before the current clip remain saved.",
        flush=True,
    )
    for index in range(1, clip_count + 1):
        seconds = min(clip_seconds, total_seconds - ((index - 1) * clip_seconds))
        path = destination / f"{_timestamp()}-{index:04d}.wav"
        print(f"negative clip {index}/{clip_count}: recording {seconds:.1f}s...", flush=True)
        _record_clip(path, seconds, device, args.channels)
        print(f"saved {path}")
    return 0


def _read_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as src:
        channels = src.getnchannels()
        sample_width = src.getsampwidth()
        sample_rate = src.getframerate()
        frame_count = src.getnframes()
        payload = src.readframes(frame_count)
    if sample_width != 2:
        raise ValueError(f"only 16-bit PCM WAV is supported: {path}")
    audio = np.frombuffer(payload, dtype="<i2")
    if channels > 1:
        audio = audio.reshape(-1, channels)
        channel_rms = [float(np.sqrt(np.mean(ch.astype(np.float64) ** 2))) for ch in audio.T]
        # Production requests one PortAudio channel. Replay channel zero while
        # retaining stereo levels to expose a receiver/channel wiring issue.
        mono = audio[:, 0].copy()
    else:
        mono = audio.reshape(-1).copy()
        channel_rms = [float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))]
    rms = channel_rms[0] if channel_rms else 0.0
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-6) / 32768.0)
    positive_levels = [v for v in channel_rms if v > 1e-6]
    imbalance_db = 0.0
    if len(positive_levels) > 1:
        imbalance_db = 20.0 * math.log10(max(positive_levels) / min(positive_levels))
    duration = len(mono) / float(sample_rate)
    return mono, sample_rate, duration, rms_dbfs, imbalance_db


def _production_frames(audio, source_rate: int, output_length: int) -> Iterable[Any]:
    import numpy as np

    from tools.wake_word import SAMPLE_RATE, _resample_audio_frame

    source_length = max(1, int(round(output_length * source_rate / SAMPLE_RATE)))
    for offset in range(0, len(audio), source_length):
        block = audio[offset : offset + source_length]
        if len(block) < source_length:
            block = np.pad(block, (0, source_length - len(block)))
        yield _resample_audio_frame(np, block, output_length)
    # KWS transducers may need trailing blanks to finalize a phrase.
    for _ in range(max(1, int(math.ceil(SAMPLE_RATE / output_length)))):
        yield np.zeros(output_length, dtype=np.int16)


def _parse_csv(value: str | None, cast, fallback: list[Any]) -> list[Any]:
    if value is None:
        return fallback
    parsed = []
    for item in value.split(","):
        item = item.strip()
        if item:
            parsed.append(cast(item))
    if not parsed:
        raise ValueError("parameter grid must contain at least one value")
    return parsed


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * fraction))
    return ordered[index]


def _candidate_grid(cfg: dict[str, Any], args: argparse.Namespace):
    from tools.wake_word import _sensitivity

    sub = cfg.get("sherpa") if isinstance(cfg.get("sherpa"), dict) else {}
    mapped = 0.05 + 0.4 * _sensitivity(cfg)
    current_threshold = sub.get("keywords_threshold")
    if current_threshold is None:
        current_threshold = mapped
    thresholds = _parse_csv(args.thresholds, float, [float(current_threshold)])
    scores = _parse_csv(args.scores, float, [float(sub.get("keywords_score", 1.0))])
    paths = _parse_csv(args.active_paths, int, [int(sub.get("max_active_paths", 4))])
    blanks = _parse_csv(args.trailing_blanks, int, [int(sub.get("num_trailing_blanks", 1))])
    if any(
        not math.isfinite(value) or value < 0.0 or value > 1.0
        for value in thresholds
    ):
        raise ValueError("--thresholds values must be between 0 and 1")
    if any(
        not math.isfinite(value) or value < 0.0 or value > 10.0
        for value in scores
    ):
        raise ValueError("--scores values must be between 0 and 10")
    if any(value < 1 or value > 64 for value in paths):
        raise ValueError("--active-paths values must be between 1 and 64")
    if any(value < 0 or value > 20 for value in blanks):
        raise ValueError("--trailing-blanks values must be between 0 and 20")
    for threshold in thresholds:
        for score in scores:
            for active_paths in paths:
                for trailing_blanks in blanks:
                    yield threshold, score, active_paths, trailing_blanks


def _alias_variants(cfg: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Compare the canonical phrase with the configured hidden aliases."""
    from tools.wake_word import _sherpa_aliases

    sub = cfg.get("sherpa") if isinstance(cfg.get("sherpa"), dict) else {}
    canonical = " ".join(str(cfg.get("phrase") or "hey hermes").strip().split())
    aliases = _sherpa_aliases(sub, canonical)
    variants = [("canonical_only", [])]
    if aliases:
        variants.append(("configured_aliases", aliases))
    return variants


def _evaluate_candidate(
    cfg: dict[str, Any],
    candidate: tuple[float, float, int, int],
    files: list[tuple[str, Path]],
    *,
    alias_mode: str = "configured",
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    from tools.wake_word import _SherpaKwsEngine

    threshold, score, active_paths, trailing_blanks = candidate
    candidate_cfg = copy.deepcopy(cfg)
    candidate_cfg["provider"] = "sherpa"
    candidate_cfg["profile_routing"] = False
    sub = candidate_cfg.setdefault("sherpa", {})
    if aliases is not None:
        sub["aliases"] = list(aliases)
    sub["keywords_threshold"] = threshold
    sub["keywords_score"] = score
    sub["max_active_paths"] = active_paths
    sub["num_trailing_blanks"] = trailing_blanks

    engine = _SherpaKwsEngine(candidate_cfg)
    positives: list[dict[str, Any]] = []
    false_wakes = 0
    negative_samples = 0
    negative_seconds = 0.0
    processing_ms: list[float] = []
    imbalances: list[float] = []
    try:
        for kind, path in files:
            engine.reset()
            audio, rate, duration, rms_dbfs, imbalance_db = _read_wav(path)
            fires = 0
            first_audio_seconds = None
            for frame_index, frame in enumerate(
                _production_frames(audio, rate, engine.frame_length), start=1
            ):
                started = time.perf_counter()
                fired = engine.process(frame)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                processing_ms.append(elapsed_ms)
                if fired:
                    fires += 1
                    if first_audio_seconds is None:
                        first_audio_seconds = frame_index * engine.frame_length / 16000.0
            imbalances.append(imbalance_db)
            if kind == "positive":
                speaker = path.parent.name
                positives.append(
                    {
                        "speaker": speaker,
                        "path": str(path),
                        "detected": fires > 0,
                        "fires": fires,
                        "rms_dbfs": round(rms_dbfs, 2),
                        "first_detection_audio_seconds": (
                            round(first_audio_seconds, 3)
                            if first_audio_seconds is not None
                            else None
                        ),
                    }
                )
            else:
                false_wakes += fires
                negative_seconds += duration
                negative_samples += 1
    finally:
        engine.close()

    by_speaker: dict[str, list[bool]] = defaultdict(list)
    for sample in positives:
        by_speaker[sample["speaker"]].append(bool(sample["detected"]))
    speaker_recall = {
        speaker: {
            "detected": sum(results),
            "samples": len(results),
            "recall": round(sum(results) / len(results), 4),
        }
        for speaker, results in sorted(by_speaker.items())
    }
    detected = sum(bool(item["detected"]) for item in positives)
    recall = detected / len(positives) if positives else 0.0
    negative_hours = negative_seconds / 3600.0
    false_rate = false_wakes / negative_hours if negative_hours else None

    ordered_by_level = sorted(positives, key=lambda item: item["rms_dbfs"])
    quartile_size = max(1, math.ceil(len(ordered_by_level) / 4)) if positives else 0
    low = ordered_by_level[:quartile_size]
    rest = ordered_by_level[quartile_size:]
    low_recall = sum(bool(x["detected"]) for x in low) / len(low) if low else None
    rest_recall = sum(bool(x["detected"]) for x in rest) / len(rest) if rest else None
    low_volume_gap = (
        max(0.0, rest_recall - low_recall)
        if low_recall is not None and rest_recall is not None
        else None
    )

    positives_sufficient = (
        len(speaker_recall) >= MIN_POSITIVE_SPEAKERS
        and all(v["samples"] >= MIN_POSITIVES_PER_SPEAKER for v in speaker_recall.values())
    )
    negatives_sufficient = negative_hours >= MIN_NEGATIVE_HOURS
    latency_ok = _percentile(processing_ms, 1.0) <= MAX_PROCESSING_MS
    false_rate_ok = false_rate is not None and false_rate <= MAX_FALSE_WAKES_PER_HOUR
    accepted = (
        positives_sufficient
        and negatives_sufficient
        and recall >= TARGET_RECALL
        and false_rate_ok
        and latency_ok
    )
    return {
        "parameters": {
            "alias_mode": alias_mode,
            "alias_count": len(sub.get("aliases") or []),
            "keywords_threshold": threshold,
            "keywords_score": score,
            "max_active_paths": active_paths,
            "num_trailing_blanks": trailing_blanks,
        },
        "accepted": accepted,
        "evidence_sufficient": positives_sufficient and negatives_sufficient,
        "positive": {
            "detected": detected,
            "samples": len(positives),
            "recall": round(recall, 4),
            "by_speaker": speaker_recall,
            "low_volume_quartile_recall": (
                round(low_recall, 4) if low_recall is not None else None
            ),
            "remaining_recall": round(rest_recall, 4) if rest_recall is not None else None,
            "low_volume_recall_gap": (
                round(low_volume_gap, 4) if low_volume_gap is not None else None
            ),
            "normalization_investigation_recommended": bool(
                low_volume_gap is not None and low_volume_gap >= 0.10
            ),
        },
        "negative": {
            "samples": negative_samples,
            "false_wakes": false_wakes if negative_samples else None,
            "hours": round(negative_hours, 4),
            "false_wakes_per_hour": round(false_rate, 4) if false_rate is not None else None,
        },
        "processing_ms": {
            "p50": round(_percentile(processing_ms, 0.50), 3),
            "p95": round(_percentile(processing_ms, 0.95), 3),
            "max": round(_percentile(processing_ms, 1.0), 3),
        },
        "max_channel_imbalance_db": round(max(imbalances, default=0.0), 2),
        "channel_investigation_recommended": max(imbalances, default=0.0) > 12.0,
        "evidence": {
            "required_positive_speakers": MIN_POSITIVE_SPEAKERS,
            "required_samples_per_speaker": MIN_POSITIVES_PER_SPEAKER,
            "required_negative_hours": MIN_NEGATIVE_HOURS,
            "positive_sufficient": positives_sufficient,
            "negative_sufficient": negatives_sufficient,
        },
    }


def _evaluate(args: argparse.Namespace) -> int:
    from tools.wake_word import load_wake_word_config

    root = _require_corpus_marker(args.corpus)
    positives = sorted((root / "positive").glob("*/*.wav"))
    negatives = sorted((root / "negative").glob("*/*.wav"))
    if not positives:
        raise ValueError(f"no positive WAV files found under {root / 'positive'}")
    if getattr(args, "positive_only", False):
        negatives = []
    files = [("positive", path) for path in positives] + [
        ("negative", path) for path in negatives
    ]
    cfg = load_wake_word_config()
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider not in {"sherpa", "sherpa-onnx", "kws", "open"}:
        raise ValueError("evaluation currently supports wake_word.provider: sherpa")
    grid = list(_candidate_grid(cfg, args))
    candidates = [
        _evaluate_candidate(
            cfg,
            candidate,
            files,
            alias_mode=alias_mode,
            aliases=aliases,
        )
        for alias_mode, aliases in _alias_variants(cfg)
        for candidate in grid
    ]
    if negatives:
        candidates.sort(
            key=lambda item: (
                not item["accepted"],
                -item["positive"]["recall"],
                item["negative"]["false_wakes_per_hour"]
                if item["negative"]["false_wakes_per_hour"] is not None
                else float("inf"),
                item["processing_ms"]["p95"],
            )
        )
    else:
        # Positive-only evidence cannot justify a more permissive detector.
        # Break recall ties in favor of fewer aliases and stricter thresholds.
        candidates.sort(
            key=lambda item: (
                -item["positive"]["recall"],
                item["parameters"]["alias_count"],
                -item["parameters"]["keywords_threshold"],
                item["processing_ms"]["p95"],
            )
        )
    if candidates[0]["accepted"]:
        result = "accepted"
    elif not negatives:
        result = "positive_only"
    elif not candidates[0]["evidence_sufficient"]:
        result = "provisional"
    else:
        result = "failed"
    report = {
        "corpus": str(root),
        "mode": "full" if negatives else "positive_only",
        "input": {
            "positive_samples": len(positives),
            "negative_samples": len(negatives),
        },
        "target": {
            "recall": TARGET_RECALL,
            "max_false_wakes_per_hour": MAX_FALSE_WAKES_PER_HOUR,
            "max_processing_ms": MAX_PROCESSING_MS,
        },
        "result": result,
        "limitations": (
            []
            if negatives
            else [
                "No negative audio was evaluated; false-wake rate and final acceptance are unavailable."
            ]
        ),
        "best": candidates[0],
        "candidates": candidates,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if result in {"accepted", "positive_only"} else 1


def _purge(args: argparse.Namespace) -> int:
    if not args.confirm_delete:
        raise ValueError("purge requires --confirm-delete")
    root = _require_corpus_marker(args.corpus)
    shutil.rmtree(root)
    print(f"deleted private wake-word corpus: {root}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(corpus=_default_corpus())
    subparsers = parser.add_subparsers(dest="command", required=True)

    positive = subparsers.add_parser("record-positive", help="record intentional wake phrases")
    positive.add_argument("--speaker", required=True, help="anonymous speaker label")
    positive.add_argument("--count", type=int, default=10)
    positive.add_argument("--seconds", type=float, default=3.0)
    positive.add_argument("--device", help="PortAudio index/name; default uses Hermes config")
    positive.add_argument("--channels", type=int, choices=(1, 2), default=1)
    positive.add_argument("--automatic", action="store_true", help="do not pause for Enter")
    positive.add_argument("--consent", action="store_true", help="confirm speaker consent")
    positive.add_argument("--corpus", type=Path, default=_default_corpus())
    positive.set_defaults(handler=_record_positive)

    negative = subparsers.add_parser("record-negative", help="record ambient non-wake audio")
    negative.add_argument("--label", required=True, help="anonymous room/session label")
    negative.add_argument("--minutes", type=float, default=30.0)
    negative.add_argument("--clip-seconds", type=float, default=30.0)
    negative.add_argument("--device", help="PortAudio index/name; default uses Hermes config")
    negative.add_argument("--channels", type=int, choices=(1, 2), default=1)
    negative.add_argument("--consent", action="store_true", help="confirm participant consent")
    negative.add_argument("--corpus", type=Path, default=_default_corpus())
    negative.set_defaults(handler=_record_negative)

    evaluate = subparsers.add_parser("evaluate", help="replay WAVs through production Sherpa")
    evaluate.add_argument("--corpus", type=Path, default=_default_corpus())
    evaluate.add_argument(
        "--positive-only",
        action="store_true",
        help="ignore negative WAVs and produce a provisional recall-only report",
    )
    evaluate.add_argument("--thresholds", help="comma-separated keywords thresholds")
    evaluate.add_argument("--scores", help="comma-separated keyword scores")
    evaluate.add_argument("--active-paths", help="comma-separated max active paths")
    evaluate.add_argument("--trailing-blanks", help="comma-separated trailing blank counts")
    evaluate.set_defaults(handler=_evaluate)

    purge = subparsers.add_parser("purge", help="delete the marked private corpus")
    purge.add_argument("--corpus", type=Path, default=_default_corpus())
    purge.add_argument("--confirm-delete", action="store_true")
    purge.set_defaults(handler=_purge)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if getattr(args, "count", 1) < 1:
            raise ValueError("--count must be at least 1")
        if getattr(args, "seconds", 1.0) <= 0:
            raise ValueError("--seconds must be positive")
        if getattr(args, "minutes", 1.0) <= 0:
            raise ValueError("--minutes must be positive")
        if getattr(args, "clip_seconds", 1.0) <= 0:
            raise ValueError("--clip-seconds must be positive")
        return int(args.handler(args))
    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
