import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/local-ovms/benchmark_voice_keyboard.py"
CASES = ROOT / "docs/benchmarks/voice-keyboard-latency/cases.json"


def _module():
    spec = importlib.util.spec_from_file_location("benchmark_voice_keyboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cases_cover_eight_scenarios_twice():
    module = _module()
    cases = module.load_cases(CASES)
    assert len(cases) == 16
    counts = {}
    for case in cases:
        counts[case["scenario"]] = counts.get(case["scenario"], 0) + 1
        assert case["prompt"] == case["prompt"].strip()
    assert len(counts) == 8
    assert set(counts.values()) == {2}


def test_aggregation_ranks_voice_critical_path_and_preserves_failures():
    module = _module()
    runs = []
    for mode, e2e in (("keyboard", 2.0), ("voice", 9.0)):
        for index in range(2):
            runs.append({
                "case_id": f"case_{index}",
                "scenario": "sample",
                "mode": mode,
                "error": None,
                "metrics": {
                    "e2e_s": e2e,
                    "agent_turn_s": 5.0 if mode == "voice" else 1.8,
                    "post_agent_s": 2.0 if mode == "voice" else 0.1,
                    "asr_s": 1.0 if mode == "voice" else 0,
                    "pre_agent_s": 1.0 if mode == "voice" else 0.1,
                    "main_llm_s": 4.0,
                    "voice_aux_llm_s": 1.5 if mode == "voice" else 0,
                    "tool_work_s": 0.2,
                    "tts_synthesis_work_s": 0.8 if mode == "voice" else 0,
                    "audio_playback_s": 1.8 if mode == "voice" else 0,
                },
            })
    runs.append({"case_id": "failed", "scenario": "sample", "mode": "voice", "error": "boom", "metrics": {"e2e_s": 1}})
    summary = module.aggregate_results(runs)
    assert summary["recorded_runs"] == 5
    assert summary["complete_runs"] == 4
    assert summary["failed_runs"] == 1
    assert summary["by_mode"]["voice"]["median_e2e_s"] == 9.0
    assert summary["scenarios"]["sample"]["voice_minus_keyboard_s"] == 7.0
    assert summary["voice_critical_path_ranking"][0]["component"] == "agent/model/tool turn"


def test_derived_metrics_exclude_native_main_calls_from_voice_auxiliary_bucket():
    module = _module()
    row = {
        "mode": "voice",
        "prompt": "What is RAM?",
        "submitted_text": "What is RM?",
        "metrics": {},
        "aux_api_calls": [
            {"seconds": 2.0, "schema": "voice_task_route", "tool_schema_count": 0},
            {"seconds": 7.0, "schema": None, "tool_schema_count": 24},
            {"seconds": 1.0, "schema": "spoken_summary", "tool_schema_count": 0},
        ],
        "playback": [
            {"kind": "ack", "start_s": 4.0, "seconds": 1.5},
            {"kind": "final", "start_s": 12.0, "seconds": 3.0},
        ],
    }
    module.refresh_derived_metrics(row)
    assert row["metrics"]["voice_aux_llm_s"] == 3.0
    assert row["metrics"]["ack_playback_s"] == 1.5
    assert row["metrics"]["final_playback_s"] == 3.0
    assert row["metrics"]["time_to_ack_audio_s"] == 4.0
    assert row["metrics"]["asr_word_error_rate"] > 0
