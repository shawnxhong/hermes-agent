"""Exercise the shared answer capture, with noisy audio and no silence."""
from unittest.mock import Mock
import queue
import threading

import numpy as np
import pytest

from tools import voice_endpoint as ep, voice_mode as vm


@pytest.mark.parametrize('mode', ['match', 'disabled', 'missing', 'cancel', 'stream_error'])
def test_shared_answer_capture_endpoint(monkeypatch, mode):
    class Stream:
        reads = 0

        def __enter__(self):
            if mode == 'stream_error':
                raise OSError('device unavailable')
            return self

        def __exit__(self, *args):
            pass

        def read(self, block):
            self.reads += 1
            # Quiet calibration, then unbroken speech/noise: no silence end.
            return np.full((block, 1), 100 if self.reads <= 5 else 5000,
                           dtype=np.int16), False

    stream = Stream()
    sd = Mock()
    sd.InputStream.return_value = stream
    monkeypatch.setattr(vm, '_import_audio', lambda: (sd, np))
    monkeypatch.setattr(ep, 'settings', lambda: None if mode == 'disabled' else {'enabled': True})
    detector = Mock()
    callback = []
    detector.start.side_effect = lambda cb, *args: callback.append(cb)
    detector.feed.side_effect = lambda pcm: callback[0]() if mode == 'match' else None

    def make_detector(cfg):
        assert stream.reads == 0  # no cold initialization after mic capture
        if mode == 'missing':
            raise RuntimeError('no cached model')
        return detector

    monkeypatch.setattr(ep, 'EndPhraseDetector', make_detector)
    write = Mock(return_value='answer.wav')
    monkeypatch.setattr(vm.AudioRecorder, '_write_wav', write)
    phases = []
    result = vm.full_duplex_listen(
        lambda: mode == 'cancel',
        on_trigger=phases.append, calibration_ms=150, max_utterance_ms=600,
    )
    if mode in {'cancel', 'stream_error'}:
        assert result is None
        write.assert_not_called()
    else:
        assert result == 'answer.wav'
        assert phases == ['generation']
        if mode == 'match':
            assert stream.reads < 20  # matched pre-roll, no trailing silence
            detector.feed.assert_called_once()
        else:
            assert stream.reads >= 30  # disabled/missing keep max-duration cap
    sd.InputStream.assert_called_once()
    if mode not in {'disabled', 'missing'}:
        detector.close.assert_called_once()


@pytest.mark.parametrize('text,expected', [
    ("Yes, that's all.", 'Yes'),
    ("No, that’s all.", 'No'),
    ("Five days from Sydney. That's all.", 'Five days from Sydney.'),
    ("That's all.", ''),
])
def test_answer_transcription_strips_control_phrase(monkeypatch, text, expected):
    from tools import transcription_tools
    monkeypatch.setattr(ep, 'settings', lambda: {'enabled': True})
    monkeypatch.setattr(transcription_tools, 'transcribe_audio',
                        lambda *a, **k: {'success': True, 'transcript': text})
    assert vm.transcribe_recording('answer.wav')['transcript'] == expected


@pytest.mark.parametrize('kind,text,expected', [
    ('approval', "Allow once, that's all.", 'once'),
    ('approval', "Deny, that's all.", 'deny'),
    ('clarify', "Sydney, that's all.", 'Sydney'),
])
def test_modal_answer_keeps_routing_after_cleanup(monkeypatch, tmp_path, kind, text, expected):
    from cli import HermesCLI
    from tools import transcription_tools
    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_stt_model = lambda: 'local'
    cli._voice_barge_phase = 'generation'
    cli._voice_barge_capture = threading.Event()
    cli._voice_clarify_response_queue = None
    cli._voice_approval_response_queue = None
    cli._voice_clarify_listening = False
    cli._voice_approval_listening = False
    cli._voice_clarify_multi_select = False
    cli._voice_approval_choices = ['once', 'session', 'always', 'deny']
    cli._voice_clarify_choices = []
    answer = queue.Queue()
    setattr(cli, f'_voice_{kind}_response_queue', answer)
    setattr(cli, f'_voice_{kind}_listening', True)
    monkeypatch.setattr(ep, 'settings', lambda: {'enabled': True})
    monkeypatch.setattr(transcription_tools, 'transcribe_audio',
                        lambda *a, **k: {'success': True, 'transcript': text})
    wav = tmp_path / 'answer.wav'
    wav.touch()
    cli._voice_submit_barge_utterance(str(wav), clarify_only=True)
    assert answer.get_nowait() == expected
    assert not wav.exists()
