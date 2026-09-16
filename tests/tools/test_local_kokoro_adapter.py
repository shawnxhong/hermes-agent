"""Behavior tests for the external command adapter; no model/audio device needed."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest


@pytest.fixture
def adapter():
    path = Path(__file__).resolve().parents[2] / "scripts/local-ovms/kokoro_tts.py"
    spec = importlib.util.spec_from_file_location("local_kokoro_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_english_preserves_voice_speed_and_frontend(adapter):
    engine = Mock()
    engine.create.return_value = (np.ones(24, dtype=np.float32), 24000)
    result = adapter.synthesize_mixed(
        engine, "Hello Intel, I'm here.", chinese_voice="zf_001",
        english_voice="af_maple", speed=1.0,
    )
    engine.create.assert_called_once_with(
        "Hello Intel, I'm here.", voice="af_maple", speed=1.0,
        lang="en-us", is_phonemes=False,
    )
    assert result[1] == 24000


def test_empty_text_does_not_call_engine(adapter):
    engine = Mock()
    with pytest.raises(ValueError, match="empty"):
        adapter.synthesize_mixed(engine, "  ", chinese_voice="zf_001",
                                 english_voice="af_maple", speed=1)
    engine.create.assert_not_called()


def test_cli_explicit_model_directory_and_wav_output(adapter, tmp_path, monkeypatch):
    import sys
    import types
    engine = Mock()
    engine.create.return_value = (np.zeros(240, dtype=np.float32), 24000)
    constructor = Mock(return_value=engine)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=constructor))
    source, output = tmp_path / "input.txt", tmp_path / "speech.wav"
    source.write_text("Ready.")
    adapter.main(["--input", str(source), "--output", str(output),
                  "--model-dir", str(tmp_path / "models")])
    constructor.assert_called_once_with(
        str(tmp_path / "models/kokoro-v1.1-zh.onnx"),
        str(tmp_path / "models/voices-v1.1-zh.bin"),
        vocab_config=str(tmp_path / "models/config.json"),
    )
    data, rate = adapter.sf.read(output)
    assert rate == 24000 and len(data) == 240
