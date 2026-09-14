from unittest.mock import Mock

import numpy as np
import pytest

from tools import voice_mode as vm


@pytest.mark.parametrize('configured,explicit,expected', [
    (4, None, 4.02), (4, 600, .6), ('invalid', None, 1.26),
])
def test_answer_window_honors_silence_setting(monkeypatch, configured, explicit, expected):
    class Stream:
        reads = 0
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n):
            self.reads += 1
            level = 5000 if 5 < self.reads <= 35 else 100
            return np.full((n, 1), level, dtype=np.int16), False
    stream = Stream()
    sd = Mock()
    sd.InputStream.return_value = stream
    monkeypatch.setattr(vm, '_import_audio', lambda: (sd, np))
    monkeypatch.setattr('hermes_cli.config.load_config',
                        lambda: {'voice': {'silence_duration': configured}})
    monkeypatch.setattr(vm.AudioRecorder, '_write_wav', lambda data: 'answer.wav')
    assert vm.full_duplex_listen(lambda: False, calibration_ms=150,
                                 endpoint_silence_ms=explicit) == 'answer.wav'
    assert (stream.reads - 35) * .03 == pytest.approx(expected)
