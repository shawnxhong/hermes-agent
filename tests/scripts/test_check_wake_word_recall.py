"""Tests for the local, private wake-word replay evaluator."""

import argparse
import importlib.util
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
