"""Tests for the local, private wake-word replay evaluator."""

import argparse
import copy
import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "local-ovms"
    / "check_wake_word_recall.py"
)
_SPEC = importlib.util.spec_from_file_location("check_wake_word_recall", _SCRIPT)
recall = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(recall)


def _grid_args(**overrides):
    values = {
        "thresholds": None,
        "scores": None,
        "active_paths": None,
        "trailing_blanks": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_private_corpus_requires_marker_and_rejects_broad_paths(tmp_path):
    root = tmp_path / "wake-corpus"
    with pytest.raises(ValueError, match="missing"):
        recall._require_corpus_marker(root)

    prepared = recall._prepare_corpus(root)
    assert recall._require_corpus_marker(prepared) == prepared.resolve()
    assert (prepared / recall.MARKER).is_file()

    with pytest.raises(ValueError, match="unsafe"):
        recall._require_corpus_marker(Path.home())
    with pytest.raises(ValueError, match="unsafe"):
        recall._prepare_corpus(recall.REPO_ROOT)
    with pytest.raises(ValueError, match="name contains 'wake'"):
        recall._prepare_corpus(tmp_path / "recordings")


def test_parameter_grid_uses_current_effective_values():
    cfg = {
        "sensitivity": 0.30,
        "sherpa": {
            "keywords_threshold": None,
            "keywords_score": 1.0,
            "max_active_paths": 4,
            "num_trailing_blanks": 1,
        },
    }
    assert list(recall._candidate_grid(cfg, _grid_args())) == [
        (pytest.approx(0.17), 1.0, 4, 1)
    ]


def test_alias_variants_include_canonical_control_and_configured_aliases():
    assert recall._alias_variants(
        {
            "phrase": "Hi Intel",
            "sherpa": {"aliases": ["High Intel", "hi intel", "Hi in tell"]},
        }
    ) == [
        ("canonical_only", []),
        ("configured_aliases", ["High Intel", "Hi in tell"]),
    ]


def _positive_args(root, **overrides):
    values = {
        "consent": True,
        "corpus": root,
        "speaker": "speaker-1",
        "device": None,
        "count": 2,
        "seconds": 0.1,
        "channels": 1,
        "automatic": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_new_positive_batch_replaces_same_speaker_only_after_success(
    monkeypatch, tmp_path
):
    import tools.wake_word as wake_word

    root = recall._prepare_corpus(tmp_path / "wake-corpus")
    destination = root / "positive" / "speaker-1"
    destination.mkdir(parents=True)
    (destination / "old.wav").write_bytes(b"old")
    monkeypatch.setattr(wake_word, "load_wake_word_config", lambda: {})
    monkeypatch.setattr(recall.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(recall, "_timestamp", lambda: "new")
    monkeypatch.setattr(
        recall,
        "_record_clip",
        lambda path, *_args: path.write_bytes(b"new"),
    )

    assert recall._record_positive(_positive_args(root)) == 0
    assert sorted(path.name for path in destination.iterdir()) == [
        "new-001.wav",
        "new-002.wav",
    ]
    assert all(path.read_bytes() == b"new" for path in destination.iterdir())


def test_failed_positive_batch_keeps_previous_speaker_samples(monkeypatch, tmp_path):
    import tools.wake_word as wake_word

    root = recall._prepare_corpus(tmp_path / "wake-corpus")
    destination = root / "positive" / "speaker-1"
    destination.mkdir(parents=True)
    old = destination / "old.wav"
    old.write_bytes(b"old")
    monkeypatch.setattr(wake_word, "load_wake_word_config", lambda: {})
    monkeypatch.setattr(recall.time, "sleep", lambda _seconds: None)

    def _fail_recording(*_args):
        raise OSError("microphone disconnected")

    monkeypatch.setattr(recall, "_record_clip", _fail_recording)
    with pytest.raises(OSError, match="disconnected"):
        recall._record_positive(_positive_args(root))

    assert old.read_bytes() == b"old"
    assert [path.name for path in destination.parent.iterdir()] == ["speaker-1"]


def test_parameter_grid_rejects_non_finite_or_out_of_range_values():
    cfg = {"sensitivity": 0.30, "sherpa": {}}
    with pytest.raises(ValueError, match="thresholds"):
        list(recall._candidate_grid(cfg, _grid_args(thresholds="nan")))
    with pytest.raises(ValueError, match="active-paths"):
        list(recall._candidate_grid(cfg, _grid_args(active_paths="0")))


def test_acceptance_requires_multi_speaker_and_eight_negative_hours(
    monkeypatch, tmp_path
):
    import tools.wake_word as wake_word

    class _FakeEngine:
        frame_length = 1280

        def __init__(self, _cfg):
            self.clip = -1

        def reset(self):
            self.clip += 1

        def process(self, _frame):
            return self.clip < 10

        def close(self):
            pass

    monkeypatch.setattr(wake_word, "_SherpaKwsEngine", _FakeEngine)
    monkeypatch.setattr(recall, "_production_frames", lambda *args: [[0]])
    negative_duration = {"seconds": 8 * 3600.0}

    def _fake_read(path):
        duration = negative_duration["seconds"] if path.name.startswith("negative") else 3.0
        return [], 16000, duration, -25.0, 0.0

    monkeypatch.setattr(recall, "_read_wav", _fake_read)
    files = []
    for speaker in ("speaker-1", "speaker-2"):
        for index in range(5):
            files.append(("positive", tmp_path / speaker / f"positive-{index}.wav"))
    files.append(("negative", tmp_path / "room" / "negative-0.wav"))

    result = recall._evaluate_candidate(
        {"phrase": "Hi Intel", "sherpa": {}},
        (0.17, 1.0, 4, 1),
        files,
    )
    assert result["accepted"] is True
    assert result["evidence_sufficient"] is True
    assert result["positive"]["recall"] == 1.0
    assert result["negative"]["false_wakes"] == 0

    negative_duration["seconds"] = 30 * 60.0
    result = recall._evaluate_candidate(
        {"phrase": "Hi Intel", "sherpa": {}},
        (0.17, 1.0, 4, 1),
        files,
    )
    assert result["accepted"] is False
    assert result["evidence"]["negative_sufficient"] is False


def test_evaluate_without_negatives_reports_positive_only(
    monkeypatch, tmp_path, capsys
):
    import tools.wake_word as wake_word

    root = recall._prepare_corpus(tmp_path / "wake-corpus")
    positive = root / "positive" / "speaker-1" / "sample.wav"
    positive.parent.mkdir(parents=True)
    positive.write_bytes(b"fixture")
    monkeypatch.setattr(
        wake_word,
        "load_wake_word_config",
        lambda: {
            "provider": "sherpa",
            "phrase": "Hi Intel",
            "sherpa": {"aliases": ["High Intel"]},
        },
    )
    monkeypatch.setattr(
        recall,
        "_candidate_grid",
        lambda _cfg, _args: [(0.17, 1.0, 4, 1)],
    )
    template = {
        "accepted": False,
        "evidence_sufficient": False,
        "positive": {"recall": 1.0},
        "negative": {"false_wakes_per_hour": None},
        "processing_ms": {"p95": 1.0},
    }

    def _fake_candidate(_cfg, _candidate, _files, *, alias_mode, aliases):
        result = copy.deepcopy(template)
        result["parameters"] = {
            "alias_mode": alias_mode,
            "alias_count": len(aliases),
            "keywords_threshold": 0.17,
        }
        return result

    monkeypatch.setattr(recall, "_evaluate_candidate", _fake_candidate)
    args = _grid_args()
    args.corpus = root
    args.positive_only = False
    assert recall._evaluate(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "positive_only"
    assert report["result"] == "positive_only"
    assert report["input"] == {"positive_samples": 1, "negative_samples": 0}
    assert [item["parameters"]["alias_mode"] for item in report["candidates"]] == [
        "canonical_only",
        "configured_aliases",
    ]
