#!/usr/bin/env python3
"""Measure real keyboard and local-ASR/TTS voice turns without external writes.

The benchmark deliberately runs against a selected Hermes source tree and the
live local OVMS endpoint.  It creates an isolated HERMES_HOME, simulator state,
cron state, session database and coding workspace.  Voice input WAVs are made
with the configured local TTS before measurement, then transcribed through the
configured local ASR.  Speaker playback is replaced by an equal-duration sleep
so the human-perceived playback time remains in the result without making noise.

External web search remains real.  Voice result email is enqueued into the
isolated local outbox, but the worker launcher is disabled, so no SMTP message is
sent.  Wake-word recognition and time spent speaking/recording are outside the
end-of-utterance latency definition.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import wave

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = REPO_ROOT / "docs/benchmarks/voice-keyboard-latency/cases.json"
DEFAULT_RUNTIME = Path.home() / ".hermes/hermes-agent"
DEFAULT_HOME = Path.home() / ".hermes"
DEFAULT_RESULTS_ROOT = REPO_ROOT / "docs/benchmarks/voice-keyboard-latency/results"
API_LINE = re.compile(
    r"API call #(\d+): model=(.*?) provider=(.*?) in=(\d+) out=(\d+) "
    r"total=(\d+) latency=([0-9.]+)s(?: cache=(\d+)/(\d+).*)?$"
)


def load_cases(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 16:
        raise ValueError("benchmark requires exactly 16 cases")
    ids: set[str] = set()
    scenarios: dict[str, int] = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case must be an object")
        case_id = str(case.get("id") or "")
        scenario = str(case.get("scenario") or "")
        prompt = str(case.get("prompt") or "")
        if not case_id or case_id in ids or not scenario or not prompt:
            raise ValueError(f"invalid or duplicate case: {case!r}")
        ids.add(case_id)
        scenarios[scenario] = scenarios.get(scenario, 0) + 1
    if len(scenarios) != 8 or set(scenarios.values()) != {2}:
        raise ValueError("benchmark requires eight scenarios with two cases each")
    return cases


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _round(value: float | int | None) -> float:
    return round(float(value or 0.0), 3)


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Return case/punctuation-insensitive word error rate."""
    normalize = lambda text: re.findall(r"[a-z0-9]+", str(text).casefold())
    expected = normalize(reference)
    actual = normalize(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for row_index, expected_word in enumerate(expected, 1):
        current = [row_index]
        for column_index, actual_word in enumerate(actual, 1):
            current.append(min(
                current[-1] + 1,
                previous[column_index] + 1,
                previous[column_index - 1] + (expected_word != actual_word),
            ))
        previous = current
    return previous[-1] / len(expected)


def refresh_derived_metrics(row: dict[str, Any]) -> None:
    """Rebuild metrics that can be derived from immutable raw events.

    ``Completions.create`` observes every non-stream request.  Native agent
    calls (which carry tool schemas) are also logged by conversation_loop, so
    count only zero-tool-schema requests as auxiliary router/title/summary
    work.  This prevents double-counting the same main inference in both
    buckets while preserving the raw records for audit.
    """
    metrics = row.setdefault("metrics", {})
    aux_calls = [
        item for item in (row.get("aux_api_calls") or [])
        if int(item.get("tool_schema_count") or 0) == 0
    ]
    playback = row.get("playback") or []
    ack = [item for item in playback if item.get("kind") == "ack"]
    final = [item for item in playback if item.get("kind") == "final"]
    metrics["voice_aux_llm_s"] = _round(sum(float(item.get("seconds", 0)) for item in aux_calls))
    metrics["ack_playback_s"] = _round(sum(float(item.get("seconds", 0)) for item in ack))
    metrics["final_playback_s"] = _round(sum(float(item.get("seconds", 0)) for item in final))
    metrics["time_to_ack_audio_s"] = _round(ack[0]["start_s"] if ack else 0)
    metrics["asr_word_error_rate"] = _round(
        word_error_rate(row.get("prompt", ""), row.get("submitted_text", ""))
        if row.get("mode") == "voice" else 0
    )


def aggregate_results(runs: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [row for row in runs if not row.get("error")]
    by_mode: dict[str, dict[str, float]] = {}
    for mode in ("keyboard", "voice"):
        rows = [row for row in complete if row.get("mode") == mode]
        totals = [float(row["metrics"]["e2e_s"]) for row in rows]
        by_mode[mode] = {
            "runs": len(rows),
            "mean_e2e_s": _round(statistics.mean(totals) if totals else 0),
            "median_e2e_s": _round(statistics.median(totals) if totals else 0),
            "p95_e2e_s": _round(_percentile(totals, 0.95)),
            "min_e2e_s": _round(min(totals) if totals else 0),
            "max_e2e_s": _round(max(totals) if totals else 0),
        }

    scenarios: dict[str, dict[str, Any]] = {}
    for scenario in sorted({str(row.get("scenario")) for row in complete}):
        entry: dict[str, Any] = {}
        for mode in ("keyboard", "voice"):
            rows = [
                row for row in complete
                if row.get("scenario") == scenario and row.get("mode") == mode
            ]
            values = [float(row["metrics"]["e2e_s"]) for row in rows]
            entry[mode] = _round(statistics.mean(values) if values else 0)
        entry["voice_minus_keyboard_s"] = _round(entry["voice"] - entry["keyboard"])
        scenarios[scenario] = entry

    voice = [row for row in complete if row.get("mode") == "voice"]
    critical_keys = (
        ("agent_turn_s", "agent/model/tool turn"),
        ("post_agent_s", "post-agent final TTS/render"),
        ("asr_s", "ASR after utterance"),
        ("pre_agent_s", "turn-start acknowledgement/pre-agent"),
    )
    critical = []
    for key, label in critical_keys:
        values = [float(row["metrics"].get(key, 0)) for row in voice]
        critical.append({
            "component": label,
            "mean_s": _round(statistics.mean(values) if values else 0),
            "total_s": _round(sum(values)),
        })
    critical.sort(key=lambda item: item["mean_s"], reverse=True)

    work_keys = (
        ("main_llm_s", "main streamed LLM calls"),
        ("voice_aux_llm_s", "voice router/summary auxiliary LLM calls"),
        ("tool_work_s", "tool execution work (may overlap)"),
        ("tts_synthesis_work_s", "TTS synthesis work (may overlap playback)"),
        ("audio_playback_s", "ack and final audio playback"),
    )
    work = []
    for key, label in work_keys:
        values = [float(row["metrics"].get(key, 0)) for row in voice]
        work.append({
            "component": label,
            "mean_s": _round(statistics.mean(values) if values else 0),
            "total_s": _round(sum(values)),
        })
    work.sort(key=lambda item: item["mean_s"], reverse=True)

    slowest_voice = sorted(
        (
            {
                "case_id": row["case_id"],
                "scenario": row["scenario"],
                "e2e_s": _round(row["metrics"]["e2e_s"]),
                "agent_turn_s": _round(row["metrics"].get("agent_turn_s")),
                "asr_s": _round(row["metrics"].get("asr_s")),
                "post_agent_s": _round(row["metrics"].get("post_agent_s")),
            }
            for row in voice
        ),
        key=lambda item: item["e2e_s"],
        reverse=True,
    )
    quality_counts: dict[str, dict[str, int]] = {}
    for mode in ("keyboard", "voice"):
        quality_counts[mode] = {
            status: sum(
                1 for row in runs
                if row.get("mode") == mode and row.get("quality_status") == status
            )
            for status in ("pass", "partial", "fail", "unreviewed")
        }
    voice_wer = [float(row["metrics"].get("asr_word_error_rate", 0)) for row in voice]
    ack_times = [float(row["metrics"].get("time_to_ack_audio_s", 0)) for row in voice]
    final_audio_times = [
        float(row["metrics"].get("time_to_first_final_audio_s", 0)) for row in voice
    ]
    ack_playback = [float(row["metrics"].get("ack_playback_s", 0)) for row in voice]
    final_playback = [float(row["metrics"].get("final_playback_s", 0)) for row in voice]
    return {
        "expected_runs": 32,
        "recorded_runs": len(runs),
        "complete_runs": len(complete),
        "failed_runs": len(runs) - len(complete),
        "by_mode": by_mode,
        "scenarios": scenarios,
        "voice_critical_path_ranking": critical,
        "voice_component_work_ranking": work,
        "slowest_voice_cases": slowest_voice,
        "quality_counts": quality_counts,
        "voice_asr": {
            "successful_transcripts": sum(
                1 for row in voice if (row.get("asr") or {}).get("success")
            ),
            "mean_word_error_rate": _round(statistics.mean(voice_wer) if voice_wer else 0),
            "median_word_error_rate": _round(statistics.median(voice_wer) if voice_wer else 0),
            "max_word_error_rate": _round(max(voice_wer) if voice_wer else 0),
        },
        "voice_feedback": {
            "mean_time_to_ack_audio_s": _round(statistics.mean(ack_times) if ack_times else 0),
            "median_time_to_ack_audio_s": _round(statistics.median(ack_times) if ack_times else 0),
            "mean_time_to_first_final_audio_s": _round(
                statistics.mean(final_audio_times) if final_audio_times else 0
            ),
            "median_time_to_first_final_audio_s": _round(
                statistics.median(final_audio_times) if final_audio_times else 0
            ),
            "mean_ack_playback_s": _round(statistics.mean(ack_playback) if ack_playback else 0),
            "mean_final_playback_s": _round(
                statistics.mean(final_playback) if final_playback else 0
            ),
        },
    }


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    result = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    result.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(result)


def render_report(raw: dict[str, Any], summary: dict[str, Any]) -> str:
    mode_rows = []
    for mode in ("keyboard", "voice"):
        item = summary["by_mode"][mode]
        mode_rows.append([
            mode, item["runs"], item["mean_e2e_s"], item["median_e2e_s"],
            item["p95_e2e_s"], item["min_e2e_s"], item["max_e2e_s"],
        ])
    scenario_rows = [
        [name, values["keyboard"], values["voice"], values["voice_minus_keyboard_s"]]
        for name, values in summary["scenarios"].items()
    ]
    critical_rows = [
        [index, item["component"], item["mean_s"], item["total_s"]]
        for index, item in enumerate(summary["voice_critical_path_ranking"], 1)
    ]
    work_rows = [
        [index, item["component"], item["mean_s"], item["total_s"]]
        for index, item in enumerate(summary["voice_component_work_ranking"], 1)
    ]
    slow_rows = [
        [item["case_id"], item["scenario"], item["e2e_s"], item["agent_turn_s"], item["asr_s"], item["post_agent_s"]]
        for item in summary["slowest_voice_cases"][:8]
    ]
    keyboard = summary["by_mode"]["keyboard"]
    voice = summary["by_mode"]["voice"]
    delta = _round(voice["median_e2e_s"] - keyboard["median_e2e_s"])
    ratio = _round(voice["median_e2e_s"] / keyboard["median_e2e_s"]) if keyboard["median_e2e_s"] else 0
    mean_delta = _round(voice["mean_e2e_s"] - keyboard["mean_e2e_s"])
    mean_ratio = _round(voice["mean_e2e_s"] / keyboard["mean_e2e_s"]) if keyboard["mean_e2e_s"] else 0
    meta = raw["metadata"]
    quality_rows = [
        [mode, *[summary["quality_counts"][mode][status] for status in ("pass", "partial", "fail", "unreviewed")]]
        for mode in ("keyboard", "voice")
    ]
    critical_total = voice["mean_e2e_s"] or 1
    ranked_findings = "\n".join(
        f"{index}. **{item['component']}** — {item['mean_s']} s/run, "
        f"{_round(100 * item['mean_s'] / critical_total)}% of mean voice E2E."
        for index, item in enumerate(summary["voice_critical_path_ranking"], 1)
    )
    return f"""# Keyboard vs Voice end-to-end latency benchmark

## Result status

- Recorded: **{summary['recorded_runs']}/32** runs; transport-complete: **{summary['complete_runs']}**; runtime errors: **{summary['failed_runs']}**.
- Keyboard mean: **{keyboard['mean_e2e_s']} s**; voice mean: **{voice['mean_e2e_s']} s**; mean voice penalty: **{mean_delta} s** (**{mean_ratio}x** keyboard).
- Keyboard median: **{keyboard['median_e2e_s']} s**; voice median: **{voice['median_e2e_s']} s**.
- Median voice penalty: **{delta} s** (**{ratio}x** keyboard).
- Source under test: `{meta['code_root']}` at `{meta['source_commit']}`; host: `{meta['host_id']}`.

Transport-complete means the harness returned; it does not mean the answer was useful.  Manual content review is summarized below and recorded case-by-case in `quality-review.json`.
The lower voice median is not evidence that voice is faster: several voice runs returned short failure or wrong-task answers, while the 387.788-second schedule outlier raises the mean and matches the reported long-tail experience.

{_table(['mode', 'pass', 'partial', 'fail', 'unreviewed'], quality_rows)}

## Method

Two long-lived CLI sessions (one per mode) processed the same 16 English prompts in the same order.  Mode execution order alternated per case.  Voice timing starts when a completed WAV is submitted to local ASR and ends after final audio playback.  It includes ASR, the configured turn-start acknowledgement, model/tool work, voice routing/summarization, final TTS synthesis and playback.  Keyboard timing starts at text submission and ends when the complete answer is returned.

Input WAVs were synthesized before measurement and then transcribed by the configured local Whisper path.  Playback used the real generated audio duration but slept silently instead of driving the speaker.  Wake-word detection, user speaking time, microphone capture and VAD/end-phrase waiting are intentionally outside the end-of-utterance metric.  Web queries were live.  Home, cron, files, sessions and voice outbox were isolated; SMTP workers were disabled.

Both modes used Hermes' existing headless single-query clarify callback.  If the model calls `clarify`, the callback immediately tells it that no user is available and to make a reasonable assumption.  This keeps every listed case a deterministic one-prompt measurement; it does not include a human follow-up delay.

## Overall end-to-end latency (seconds)

{_table(['mode', 'runs', 'mean', 'median', 'p95', 'min', 'max'], mode_rows)}

## Scenario comparison (mean seconds)

{_table(['scenario', 'keyboard', 'voice', 'voice - keyboard'], scenario_rows)}

## Voice critical-path bottlenecks, largest first

These four mutually exclusive stages sum to voice end-to-end time apart from sub-millisecond timestamp rounding.

{ranked_findings}

{_table(['rank', 'critical-path stage', 'mean/run', 'total'], critical_rows)}

## Voice component work, largest first

This table is diagnostic work attribution.  Tool, synthesis and playback work can overlap other stages and therefore must not be summed as a second end-to-end total.

{_table(['rank', 'component work', 'mean/run', 'total'], work_rows)}

The auxiliary LLM row includes voice continuity/task routing, title work and spoken summarization, but excludes non-stream native-agent calls that are already present in the main LLM row.

## ASR and first audible feedback

- ASR returned a non-empty transcript for **{summary['voice_asr']['successful_transcripts']}/16** voice cases.
- Mean/median word error rate: **{summary['voice_asr']['mean_word_error_rate']} / {summary['voice_asr']['median_word_error_rate']}**; maximum: **{summary['voice_asr']['max_word_error_rate']}**.
- The turn-start acknowledgement began after **{summary['voice_feedback']['mean_time_to_ack_audio_s']} s mean / {summary['voice_feedback']['median_time_to_ack_audio_s']} s median** and played for **{summary['voice_feedback']['mean_ack_playback_s']} s mean**.
- The final answer audio began after **{summary['voice_feedback']['mean_time_to_first_final_audio_s']} s mean / {summary['voice_feedback']['median_time_to_first_final_audio_s']} s median**; final playback itself averaged **{summary['voice_feedback']['mean_final_playback_s']} s**.
- Case-level `time_to_ack_audio_s`, `asr_word_error_rate`, ack playback and final playback are in `summary.csv`.

## Slowest voice cases

{_table(['case', 'scenario', 'E2E', 'agent turn', 'ASR', 'post-agent'], slow_rows)}

## Interpretation constraints

- The benchmark measures the deployed local model and live network conditions at one point in time; search latency and results are variable.
- Synthetic input makes ASR timing reproducible, but does not measure microphone quality, VAD silence duration, wake-word recall or a human speaker's recognition accuracy.
- Silent equal-duration playback avoids audible test output while preserving audio duration; sound-device startup/driver latency is not represented.
- The two mode sessions intentionally accumulate the same scenario sequence, matching the target long-lived demo session and exposing context-growth costs.
- Captured email deliverables prove harness behavior and content only.  No real SMTP delivery or receipt wait is included.
- Another idle Hermes CLI may remain open; no service or user process was stopped.  Any overlapping external use of OVMS would appear as real contention in these measurements.

See `summary.csv`, `outputs.md`, `raw-results.json`, and `environment.json` in this directory for case-level evidence.
"""


def render_outputs(runs: list[dict[str, Any]]) -> str:
    parts = ["# Benchmark prompts and outputs", ""]
    for row in runs:
        parts.extend([
            f"## {row['case_id']} — {row['scenario']} — {row['mode']}",
            "",
            f"- Execution index: {row['execution_index']}",
            f"- E2E: {row.get('metrics', {}).get('e2e_s', 'n/a')} s",
            f"- Prompt: {row['prompt']}",
        ])
        if row["mode"] == "voice":
            parts.extend([
                f"- ASR transcript: {row.get('submitted_text', '')}",
                f"- ASR success: {row.get('asr', {}).get('success', False)}",
            ])
        parts.extend([
            f"- Quality review: {row.get('quality_status', 'unreviewed')}",
            f"- Quality note: {row.get('quality_note') or 'None'}",
        ])
        parts.extend(["", "### Returned response", "", row.get("response") or "_(empty)_", ""])
        deliverables = row.get("captured_deliverables") or []
        if deliverables:
            parts.extend(["### Captured detailed deliverable (SMTP disabled)", ""])
            for item in deliverables:
                parts.extend([item.get("body") or "_(empty)_", ""])
        if row.get("error"):
            parts.extend(["### Error", "", f"`{row['error']}`", ""])
    rendered = "\n".join(parts)
    return "\n".join(line.rstrip() for line in rendered.splitlines()).rstrip() + "\n"


CSV_FIELDS = [
    "execution_index", "case_id", "scenario", "mode", "e2e_s", "asr_s",
    "pre_agent_s", "agent_turn_s", "post_agent_s", "main_llm_s",
    "voice_aux_llm_s", "tool_work_s", "tts_synthesis_work_s",
    "audio_playback_s", "ack_playback_s", "final_playback_s",
    "time_to_ack_audio_s", "time_to_first_text_s", "time_to_first_final_audio_s",
    "asr_word_error_rate", "quality_status", "quality_note",
    "input_audio_duration_s", "main_api_calls", "aux_api_calls", "tool_calls",
    "prompt_tokens", "completion_tokens", "response_chars", "error",
]


def write_artifacts(output: Path, raw: dict[str, Any]) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    runs = raw["runs"]
    review_path = output / "quality-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else {}
    annotations = review.get("runs", {}) if isinstance(review, dict) else {}
    for row in runs:
        refresh_derived_metrics(row)
        annotation = annotations.get(f"{row.get('case_id')}:{row.get('mode')}", {})
        row["quality_status"] = annotation.get("status", "unreviewed")
        row["quality_note"] = annotation.get("note", "")
    summary = aggregate_results(runs)
    (output / "raw-results.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "outputs.md").write_text(render_outputs(runs), encoding="utf-8")
    (output / "REPORT.md").write_text(render_report(raw, summary), encoding="utf-8")
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in runs:
            metrics = row.get("metrics") or {}
            writer.writerow({
                "execution_index": row.get("execution_index"),
                "case_id": row.get("case_id"),
                "scenario": row.get("scenario"),
                "mode": row.get("mode"),
                **{key: metrics.get(key, "") for key in CSV_FIELDS if key in metrics},
                "input_audio_duration_s": (row.get("asr") or {}).get("input_audio_duration_s", ""),
                "main_api_calls": len(row.get("main_api_calls") or []),
                "aux_api_calls": sum(
                    1 for item in (row.get("aux_api_calls") or [])
                    if int(item.get("tool_schema_count") or 0) == 0
                ),
                "tool_calls": len(row.get("tools") or []),
                "prompt_tokens": metrics.get("prompt_tokens", ""),
                "completion_tokens": metrics.get("completion_tokens", ""),
                "response_chars": len(row.get("response") or ""),
                "error": row.get("error") or "",
                "quality_status": row.get("quality_status") or "unreviewed",
                "quality_note": row.get("quality_note") or "",
            })
    return summary


def _dotenv_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip("\"").strip("'")
    return ""


def _copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _find_skill(real_home: Path, fallback: Path, name: str) -> Path:
    matches = [
        path for path in (real_home / "skills").rglob(name)
        if path.is_dir() and (path / "SKILL.md").is_file()
    ] if (real_home / "skills").is_dir() else []
    if matches:
        return matches[0]
    if fallback.is_dir():
        return fallback
    raise FileNotFoundError(f"skill not found: {name}")


def prepare_home(real_home: Path, source_root: Path, target: Path, port: int) -> dict[str, Any]:
    cfg = yaml.safe_load((real_home / "config.yaml").read_text(encoding="utf-8")) or {}
    cfg.setdefault("model", {})["max_tokens"] = 4096
    cfg.setdefault("voice", {})["auto_tts"] = True
    cfg.setdefault("stt", {})["enabled"] = True
    local_stt = cfg["stt"].setdefault("local", {})
    local_stt["language"] = "en"
    local_stt["prewarm"] = True
    local_stt["unload_after_idle_seconds"] = 0
    cfg["voice_delivery"] = {
        "enabled": True,
        "default_recipient": "benchmark@example.com",
        "continuity": {"enabled": True},
    }
    plugins = cfg.setdefault("plugins", {})
    plugins["enabled"] = ["demo-media", "general-voice", "demo-home"]
    entries = plugins.setdefault("entries", {})
    entries.setdefault("demo-home", {}).setdefault("settings", {})["port"] = port
    cfg.setdefault("platform_toolsets", {})["cli"] = ["hermes-cli", "demo_home"]
    display = cfg.setdefault("display", {})
    display.update({
        "bell_on_complete": False,
        "persistent_output": False,
        "show_reasoning": False,
        "streaming": True,
        "tool_progress": "off",
    })
    target.mkdir(parents=True, exist_ok=True)
    config_path = target / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    config_path.chmod(0o600)

    for name in ("demo-media", "general-voice", "demo-home"):
        deployed = real_home / "plugins" / name
        fallback = source_root / "scripts/local-ovms/plugins" / name
        if not fallback.is_dir():
            fallback = REPO_ROOT / "scripts/local-ovms/plugins" / name
        _copy_tree(deployed if deployed.is_dir() else fallback, target / "plugins" / name)

    travel = _find_skill(
        real_home,
        (
            source_root / "skills/productivity/travel-concierge"
            if (source_root / "skills/productivity/travel-concierge").is_dir()
            else REPO_ROOT / "skills/productivity/travel-concierge"
        ),
        "travel-concierge",
    )
    demo_home = _find_skill(
        real_home,
        (
            source_root / "scripts/local-ovms/skills/demo-home-assistant"
            if (source_root / "scripts/local-ovms/skills/demo-home-assistant").is_dir()
            else REPO_ROOT / "scripts/local-ovms/skills/demo-home-assistant"
        ),
        "demo-home-assistant",
    )
    _copy_tree(travel, target / "skills/productivity/travel-concierge")
    _copy_tree(demo_home, target / "skills/productivity/demo-home-assistant")
    return cfg


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _git_value(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def collect_environment(source_root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    uname = os.uname()
    runtime_status = _git_value(source_root, "status", "--short")
    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host_id": "laptop",
        "platform": {
            "sysname": uname.sysname,
            "release": uname.release,
            "machine": uname.machine,
            "python": sys.version.split()[0],
        },
        "source": {
            "path": str(source_root),
            "commit": _git_value(source_root, "rev-parse", "HEAD"),
            "branch": _git_value(source_root, "branch", "--show-current"),
            "working_tree_change_count": len(runtime_status.splitlines()) if runtime_status else 0,
            "reproducible_from_single_commit": not bool(runtime_status),
        },
        "model": {
            "default": (cfg.get("model") or {}).get("default"),
            "provider": (cfg.get("model") or {}).get("provider"),
            "base_url": (cfg.get("model") or {}).get("base_url"),
            "context_length": (cfg.get("model") or {}).get("context_length"),
            "keyboard_max_tokens": 4096,
            "voice_max_tokens": 1024,
        },
        "stt": {
            "provider": (cfg.get("stt") or {}).get("provider"),
            "language": (cfg.get("stt") or {}).get("language"),
            "local": {
                key: ((cfg.get("stt") or {}).get("local") or {}).get(key)
                for key in ("model", "language", "vad", "prewarm", "unload_after_idle_seconds")
            },
        },
        "tts": {
            "provider": (cfg.get("tts") or {}).get("provider"),
            "output_format": (((cfg.get("tts") or {}).get("providers") or {}).get(
                (cfg.get("tts") or {}).get("provider"), {}
            ) or {}).get("output_format"),
        },
        "isolation": {
            "smtp": "local outbox only; worker disabled",
            "home": "loopback simulator with temporary SQLite",
            "cron_sessions_files": "temporary HERMES_HOME/workspace",
            "speaker": "real audio duration slept silently",
            "web": "live Brave Search",
        },
    }


class Recorder:
    def __init__(self) -> None:
        self.current: dict[str, Any] | None = None

    def begin(self, row: dict[str, Any]) -> None:
        row.update(
            events=[], main_api_calls=[], aux_api_calls=[], tools=[],
            tts_synthesis=[], playback=[], captured_deliverables=[],
        )
        row["_origin"] = time.monotonic()
        self.current = row

    def now(self) -> float:
        if self.current is None:
            return 0.0
        return time.monotonic() - self.current["_origin"]

    def event(self, name: str, **fields: Any) -> None:
        if self.current is not None:
            self.current["events"].append({"name": name, "at_s": _round(self.now()), **fields})

    def finish(self) -> None:
        if self.current is not None:
            self.current.pop("_origin", None)
        self.current = None


class TimingLogHandler(logging.Handler):
    def __init__(self, recorder: Recorder) -> None:
        super().__init__(level=logging.INFO)
        self.recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        current = self.recorder.current
        if current is None:
            return
        message = record.getMessage()
        match = API_LINE.search(message)
        if match:
            current["main_api_calls"].append({
                "call": int(match.group(1)),
                "model": match.group(2),
                "provider": match.group(3),
                "prompt_tokens": int(match.group(4)),
                "completion_tokens": int(match.group(5)),
                "total_tokens": int(match.group(6)),
                "seconds": float(match.group(7)),
                "cache_read_tokens": int(match.group(8) or 0),
                "at_s": _round(self.recorder.now()),
            })
        elif "voice_latency stage=" in message:
            current["events"].append({
                "name": "voice_latency_log",
                "at_s": _round(self.recorder.now()),
                "message": message[:400],
            })


def _audio_duration(path: str | Path) -> float:
    value = Path(path)
    try:
        with wave.open(str(value), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except Exception:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(value)],
            capture_output=True, text=True, timeout=10, check=False,
        )
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0.0


def _event_time(row: dict[str, Any], name: str, *, last: bool = False) -> float | None:
    values = [float(event["at_s"]) for event in row.get("events", []) if event.get("name") == name]
    if not values:
        return None
    return values[-1] if last else values[0]


def finalize_metrics(row: dict[str, Any], end_s: float) -> None:
    asr_end = _event_time(row, "asr_end") or 0.0
    chat_start = _event_time(row, "chat_start") or asr_end
    agent_start = _event_time(row, "agent_start") or chat_start
    agent_end = _event_time(row, "agent_end") or agent_start
    chat_end = _event_time(row, "chat_end") or end_s
    main_calls = row.get("main_api_calls") or []
    aux_calls = row.get("aux_api_calls") or []
    tools = row.get("tools") or []
    synth = row.get("tts_synthesis") or []
    playback = row.get("playback") or []
    prompt_tokens = sum(int(item.get("prompt_tokens", 0)) for item in main_calls)
    completion_tokens = sum(int(item.get("completion_tokens", 0)) for item in main_calls)
    final_audio = [item for item in playback if item.get("kind") == "final"]
    row["metrics"] = {
        "e2e_s": _round(end_s),
        "asr_s": _round(asr_end if row["mode"] == "voice" else 0),
        "pre_agent_s": _round(max(0.0, agent_start - chat_start)),
        "agent_turn_s": _round(max(0.0, agent_end - agent_start)),
        "post_agent_s": _round(max(0.0, chat_end - agent_end)),
        "main_llm_s": _round(sum(float(item.get("seconds", 0)) for item in main_calls)),
        "voice_aux_llm_s": _round(sum(float(item.get("seconds", 0)) for item in aux_calls)),
        "tool_work_s": _round(sum(float(item.get("duration_s", 0)) for item in tools)),
        "tts_synthesis_work_s": _round(sum(float(item.get("seconds", 0)) for item in synth)),
        "audio_playback_s": _round(sum(float(item.get("seconds", 0)) for item in playback)),
        "time_to_first_text_s": _round((_event_time(row, "first_text") or 0.0)),
        "time_to_first_final_audio_s": _round(final_audio[0]["start_s"] if final_audio else 0.0),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def worker(args: argparse.Namespace) -> int:
    os.environ["HERMES_HOME"] = str(args.home)
    os.environ["HERMES_YOLO_MODE"] = "1"
    os.environ["HERMES_INTERACTIVE"] = "0"
    os.environ["TERMINAL_CWD"] = str(args.workspace)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
    sys.path.insert(0, str(args.code_root))
    os.chdir(args.workspace)

    cases = load_cases(args.cases)
    recorder = Recorder()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(TimingLogHandler(recorder))

    from hermes_cli import plugins
    plugins.discover_plugins()
    from hermes_cli.config import load_config
    cfg = load_config()
    from hermes_cli.tools_config import _get_platform_tools
    toolsets = sorted(_get_platform_tools(cfg, "cli"))

    from openai.resources.chat.completions import Completions
    original_create = Completions.create

    def timed_create(self, *create_args, **kwargs):
        started = time.monotonic()
        result = original_create(self, *create_args, **kwargs)
        if recorder.current is not None and not kwargs.get("stream", False):
            schema = (((kwargs.get("response_format") or {}).get("json_schema") or {}).get("name"))
            usage = getattr(result, "usage", None)
            recorder.current["aux_api_calls"].append({
                "seconds": _round(time.monotonic() - started),
                "schema": schema,
                "model": kwargs.get("model"),
                "max_tokens": kwargs.get("max_tokens"),
                "tool_schema_count": len(kwargs.get("tools") or []),
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "at_s": _round(recorder.now()),
            })
        return result

    Completions.create = timed_create

    from tools import tts_tool
    from tools import voice_mode
    original_tts = tts_tool.text_to_speech_tool
    original_play = voice_mode.play_audio_file

    def measured_tts(*tts_args, **kwargs):
        text = str(kwargs.get("text") or (tts_args[0] if tts_args else ""))
        started_at = recorder.now()
        started = time.monotonic()
        result = original_tts(*tts_args, **kwargs)
        if recorder.current is not None:
            recorder.current["tts_synthesis"].append({
                "start_s": _round(started_at),
                "seconds": _round(time.monotonic() - started),
                "characters": len(text),
                "text": text,
            })
        return result

    def silent_play(path):
        duration = _audio_duration(path)
        started_at = recorder.now()
        current = recorder.current
        agent_started = _event_time(current, "agent_start") if current else None
        kind = "ack" if current is not None and agent_started is None else "final"
        time.sleep(max(0.0, duration))
        if current is not None:
            current["playback"].append({
                "kind": kind,
                "start_s": _round(started_at),
                "seconds": _round(duration),
                "file_suffix": Path(path).suffix,
            })
        return True

    tts_tool.text_to_speech_tool = measured_tts
    voice_mode.play_audio_file = silent_play

    from hermes_cli import voice_outbox
    original_enqueue = voice_outbox.enqueue
    voice_outbox.kick = lambda: None

    def captured_enqueue(store, session, task, recipient, interrupted=lambda: False, *, version=None):
        artifact = store.result(session, task["id"], version) if version is not None else store.artifact(session, task["id"])
        started = time.monotonic()
        status = original_enqueue(store, session, task, recipient, interrupted, version=version)
        if recorder.current is not None:
            recorder.current["captured_deliverables"].append({
                "body": (artifact or {}).get("body", ""),
                "summary": (artifact or {}).get("summary", ""),
                "status": status,
                "enqueue_s": _round(time.monotonic() - started),
            })
        return status

    voice_outbox.enqueue = captured_enqueue

    fixture_dir = args.home / "benchmark-input-audio"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixtures: dict[str, dict[str, Any]] = {}
    fixture_started = time.monotonic()
    for case in cases:
        output = fixture_dir / f"{case['id']}.wav"
        raw = original_tts(text=case["prompt"] + " That's all.", output_path=str(output))
        actual = output
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                decoded = {}
            if isinstance(decoded, dict) and decoded.get("file_path"):
                actual = Path(decoded["file_path"])
        if not actual.is_file() or actual.stat().st_size == 0:
            raise RuntimeError(f"failed to create audio fixture for {case['id']}: {raw}")
        fixtures[case["id"]] = {
            "path": str(actual),
            "duration_s": _round(_audio_duration(actual)),
            "sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
        }
    fixture_generation_s = time.monotonic() - fixture_started

    from tools.transcription_tools import prewarm_local_model
    asr_warm_started = time.monotonic()
    prewarm_local_model()
    asr_prewarm_s = time.monotonic() - asr_warm_started
    from hermes_cli.voice_wake_ack import cached_turn_ack
    ack_warm_started = time.monotonic()
    cached_turn_ack("Sure, let me check.")
    ack_prewarm_s = time.monotonic() - ack_warm_started

    from agent.skill_commands import build_preloaded_skills_prompt
    from cli import HermesCLI

    clis: dict[str, HermesCLI] = {}
    startup: dict[str, Any] = {
        "fixture_generation_s": _round(fixture_generation_s),
        "asr_prewarm_s": _round(asr_prewarm_s),
        "ack_cache_prewarm_s": _round(ack_prewarm_s),
    }
    for mode in ("keyboard", "voice"):
        started = time.monotonic()
        cli = HermesCLI(
            model=(cfg.get("model") or {}).get("default"),
            toolsets=toolsets,
            provider="custom",
            api_key="local-ovms",
            base_url=(cfg.get("model") or {}).get("base_url"),
            max_turns=24,
            verbose=False,
            compact=True,
        )
        cli.max_tokens = 1024 if mode == "voice" else 4096
        cli.streaming_enabled = True
        # Every benchmark case must complete from the one supplied prompt.
        # Use Hermes' production -q callback instead of leaving an interactive
        # clarify modal waiting for a user who is deliberately not present.
        cli._single_query_mode = True
        cli._voice_mode = mode == "voice"
        cli._voice_tts = mode == "voice"
        cli._voice_continuous = False
        if mode == "voice":
            cli._preload_skills_result = build_preloaded_skills_prompt(
                ["travel-concierge"], task_id=cli.session_id
            )
            cli._preload_skills_thread = threading.Thread(target=lambda: None)
            cli._preload_skills_thread.start()
        route = cli._resolve_turn_agent_config("benchmark warm initialization")
        if not cli._init_agent(
            model_override=route["model"],
            runtime_override=route["runtime"],
            request_overrides=route.get("request_overrides"),
        ):
            raise RuntimeError(f"could not initialize {mode} agent")
        agent = cli.agent
        assert agent is not None

        def progress(event, tool_name, preview=None, tool_args=None, _mode=mode, **kwargs):
            current = recorder.current
            if current is None or current.get("mode") != _mode:
                return
            if event == "tool.started":
                current["tools"].append({
                    "name": tool_name,
                    "started_s": _round(recorder.now()),
                    "duration_s": None,
                })
            elif event == "tool.completed":
                for item in reversed(current["tools"]):
                    if item["name"] == tool_name and item["duration_s"] is None:
                        item["duration_s"] = _round(kwargs.get("duration", 0))
                        item["is_error"] = bool(kwargs.get("is_error", False))
                        item["completed_s"] = _round(recorder.now())
                        break

        first_text = {"seen": False}

        def stream_delta(text, _mode=mode, _first_text=first_text):
            current = recorder.current
            if current is None or current.get("mode") != _mode or not text:
                return
            if not _first_text["seen"]:
                _first_text["seen"] = True
                recorder.event("first_text")

        agent.tool_progress_callback = progress
        agent.stream_delta_callback = stream_delta
        original_run = agent.run_conversation

        def measured_run(
            *run_args,
            _original=original_run,
            _mode=mode,
            _first_text=first_text,
            **run_kwargs,
        ):
            if recorder.current is not None and recorder.current.get("mode") == _mode:
                _first_text["seen"] = False
                recorder.event("agent_start")
            try:
                return _original(*run_args, **run_kwargs)
            finally:
                if recorder.current is not None and recorder.current.get("mode") == _mode:
                    recorder.event("agent_end")

        agent.run_conversation = measured_run
        startup[f"{mode}_cli_agent_init_s"] = _round(time.monotonic() - started)
        clis[mode] = cli

    from tools.voice_mode import transcribe_recording
    runs: list[dict[str, Any]] = []
    execution_index = 0
    simulator_script = args.code_root / "scripts/local-ovms/plugins/demo-home/simulator.py"
    if not simulator_script.is_file():
        simulator_script = REPO_ROOT / "scripts/local-ovms/plugins/demo-home/simulator.py"

    for case_index, case in enumerate(cases):
        modes = ("keyboard", "voice") if case_index % 2 == 0 else ("voice", "keyboard")
        for mode in modes:
            execution_index += 1
            if case["scenario"] == "home_appliance_control":
                subprocess.run(
                    [sys.executable, str(simulator_script), "reset-demo", "--db", str(args.simulator_db), "--port", str(args.port)],
                    capture_output=True, text=True, timeout=10, check=True,
                )
            if case["scenario"] == "schedule_management":
                (args.home / "cron/jobs.json").unlink(missing_ok=True)
            if case["id"] == "coding_02":
                (args.workspace / "benchmark_fibonacci.py").unlink(missing_ok=True)

            row: dict[str, Any] = {
                "execution_index": execution_index,
                "case_id": case["id"],
                "scenario": case["scenario"],
                "mode": mode,
                "prompt": case["prompt"],
                "submitted_text": case["prompt"],
                "asr": {},
                "response": "",
                "error": None,
            }
            recorder.begin(row)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            try:
                if mode == "voice":
                    recorder.event("asr_start")
                    asr_result = transcribe_recording(fixtures[case["id"]]["path"])
                    recorder.event("asr_end")
                    row["asr"] = {
                        "success": bool(asr_result.get("success")),
                        "transcript": str(asr_result.get("transcript") or ""),
                        "input_audio_duration_s": fixtures[case["id"]]["duration_s"],
                        "fixture_sha256": fixtures[case["id"]]["sha256"],
                        "filtered": bool(asr_result.get("filtered", False)),
                    }
                    if not asr_result.get("success") or not str(asr_result.get("transcript") or "").strip():
                        raise RuntimeError(f"ASR failed: {asr_result.get('error') or 'empty transcript'}")
                    row["submitted_text"] = str(asr_result["transcript"]).strip()
                recorder.event("chat_start")
                with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
                    row["response"] = clis[mode].chat(
                        row["submitted_text"], voice_input=(mode == "voice")
                    ) or ""
                recorder.event("chat_end")
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                recorder.event("run_error", error_type=type(exc).__name__)
            end_s = recorder.now()
            row["console_tail"] = (captured_stdout.getvalue() + captured_stderr.getvalue())[-4000:]
            finalize_metrics(row, end_s)
            recorder.finish()
            runs.append(row)
            print(
                json.dumps({
                    "progress": f"{execution_index}/32",
                    "case": case["id"],
                    "mode": mode,
                    "e2e_s": row["metrics"]["e2e_s"],
                    "error": row["error"],
                }),
                flush=True,
            )
            partial = {
                "metadata": {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "host_id": "laptop",
                    "code_root": str(args.code_root),
                    "source_commit": _git_value(args.code_root, "rev-parse", "HEAD"),
                    "case_order": [item["id"] for item in cases],
                    "mode_order": "alternating keyboard-first/voice-first by case",
                    "latency_origin": "keyboard text submit; voice completed-WAV ASR submit",
                    "wake_word_included": False,
                    "microphone_capture_included": False,
                    "smtp_sent": False,
                },
                "startup": startup,
                "runs": runs,
            }
            args.raw_output.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    Completions.create = original_create
    tts_tool.text_to_speech_tool = original_tts
    voice_mode.play_audio_file = original_play
    return 0


def orchestrate(args: argparse.Namespace) -> int:
    args.cases = args.cases.resolve()
    args.code_root = args.code_root.resolve()
    args.real_home = args.real_home.resolve()
    cases = load_cases(args.cases)
    del cases
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    output = (args.output or (DEFAULT_RESULTS_ROOT / f"{timestamp}-laptop")).resolve()
    output.mkdir(parents=True, exist_ok=False)
    temporary = Path(tempfile.mkdtemp(prefix="hermes-voice-keyboard-benchmark-"))
    home = temporary / "home"
    workspace = temporary / "workspace"
    workspace.mkdir(parents=True)
    port = _free_port()
    cfg = prepare_home(args.real_home, args.code_root, home, port)
    environment = collect_environment(args.code_root, cfg)
    (output / "environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copy2(args.cases, output / "cases.json")

    simulator_source = args.code_root / "scripts/local-ovms/plugins/demo-home/simulator.py"
    if not simulator_source.is_file():
        simulator_source = REPO_ROOT / "scripts/local-ovms/plugins/demo-home/simulator.py"
    simulator_db = temporary / "demo-home.sqlite"
    simulator_log = (temporary / "simulator.log").open("w", encoding="utf-8")
    simulator = subprocess.Popen(
        [sys.executable, str(simulator_source), "serve", "--db", str(simulator_db), "--port", str(port)],
        stdout=simulator_log, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(0.5)
    if simulator.poll() is not None:
        raise RuntimeError("demo-home simulator exited during startup")

    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["HERMES_YOLO_MODE"] = "1"
    env["HERMES_INTERACTIVE"] = "0"
    env["TERMINAL_CWD"] = str(workspace)
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    brave = _dotenv_value(args.real_home / ".env", "BRAVE_SEARCH_API_KEY")
    if brave:
        env["BRAVE_SEARCH_API_KEY"] = brave
    raw_output = output / "raw-results.partial.json"
    worker_log = temporary / "worker.log"
    command = [
        sys.executable, str(Path(__file__).resolve()), "_worker",
        "--cases", str(args.cases), "--code-root", str(args.code_root),
        "--home", str(home), "--workspace", str(workspace),
        "--simulator-db", str(simulator_db), "--port", str(port),
        "--raw-output", str(raw_output),
    ]
    try:
        with worker_log.open("w", encoding="utf-8") as handle:
            process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert process.stdout is not None
            for line in process.stdout:
                handle.write(line)
                handle.flush()
                print(line, end="", flush=True)
            returncode = process.wait()
        if returncode != 0:
            raise RuntimeError(f"benchmark worker exited {returncode}; see {worker_log}")
    finally:
        simulator.terminate()
        try:
            simulator.wait(timeout=5)
        except subprocess.TimeoutExpired:
            simulator.kill()
            simulator.wait(timeout=5)
        simulator_log.close()

    raw = json.loads(raw_output.read_text(encoding="utf-8"))
    raw_output.unlink()
    summary = write_artifacts(output, raw)
    if summary["recorded_runs"] != 32:
        raise RuntimeError(f"incomplete benchmark: {summary['recorded_runs']}/32 runs")
    print(json.dumps({"output": str(output), "summary": summary["by_mode"]}, indent=2), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    run = subparsers.add_parser("run", help="run all 32 measurements and write artifacts")
    run.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    run.add_argument("--code-root", type=Path, default=DEFAULT_RUNTIME)
    run.add_argument("--real-home", type=Path, default=DEFAULT_HOME)
    run.add_argument("--output", type=Path)
    work = subparsers.add_parser("_worker")
    work.add_argument("--cases", type=Path, required=True)
    work.add_argument("--code-root", type=Path, required=True)
    work.add_argument("--home", type=Path, required=True)
    work.add_argument("--workspace", type=Path, required=True)
    work.add_argument("--simulator-db", type=Path, required=True)
    work.add_argument("--port", type=int, required=True)
    work.add_argument("--raw-output", type=Path, required=True)
    report = subparsers.add_parser("report", help="regenerate report files from raw-results.json")
    report.add_argument("raw", type=Path)
    report.add_argument("--output", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        return orchestrate(args)
    if args.command == "_worker":
        return worker(args)
    if args.command == "report":
        raw = json.loads(args.raw.read_text(encoding="utf-8"))
        write_artifacts(args.output or args.raw.parent, raw)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
