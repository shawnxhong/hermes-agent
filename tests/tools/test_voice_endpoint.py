from types import SimpleNamespace
from unittest.mock import Mock
import threading

import numpy as np
import pytest

from tools import voice_endpoint as ep


@pytest.mark.parametrize('text,expected', [
    ('Find a restaurant. Over and out.', 'Find a restaurant.'),
    ('Yes, over and out!', 'Yes'),
    ('Over and out.', ''),
    ('Go over the weekend.', 'Go over the weekend.'),
    ('The phrase over and out is a radio sign-off.', 'The phrase over and out is a radio sign-off.'),
])
def test_only_terminal_phrase_is_removed(monkeypatch, text, expected):
    monkeypatch.setattr(ep, 'settings', lambda: {'enabled': True})
    assert ep.strip_end_phrase(text) == expected


def test_disabled_preserves_transcript(monkeypatch):
    monkeypatch.setattr(ep, 'settings', lambda: None)
    assert ep.strip_end_phrase('Over and out.') == 'Over and out.'


@pytest.mark.parametrize('rate', [16000, 48000])
def test_worker_fires_on_matching_frame_without_waiting_for_silence(rate):
    engine = Mock()
    engine.process.side_effect = [True] + [False] * 20
    d = ep.EndPhraseDetector({}, engine=engine)
    done = threading.Event()
    callback = Mock(side_effect=done.set)
    d.start(callback, rate, 400)
    # Only one loud frame: no subsequent frame, silence or post-keyword timer.
    d.feed(np.full((rate // 10, 1), 2000, dtype=np.int16))
    assert done.wait(5)
    d.stop()
    callback.assert_called_once()
    engine.process.assert_called_once()
    assert len(engine.process.call_args.args[0]) == 1600
    d.close()


def test_cancel_and_restart_drops_prior_recording():
    d = ep.EndPhraseDetector({}, engine=Mock(process=Mock(return_value=False)))
    callback = Mock()
    d.start(callback, 16000, 400)
    d.feed(np.zeros((1600, 1), dtype=np.int16))
    d.stop()
    d.start(callback, 16000, 400)
    d.stop()
    callback.assert_not_called()
    assert d.engine.reset.call_count == 2
    d.close()


def test_real_recorder_uses_one_stream_and_callback_once(monkeypatch):
    from tools import voice_mode as vm
    sd = Mock()
    monkeypatch.setattr(vm, '_import_audio', lambda: (sd, np))
    monkeypatch.setattr(vm, '_default_input_samplerate', lambda _: 16000)
    monkeypatch.setattr(ep, 'settings', lambda: {'enabled': True})
    engine = Mock(process=Mock(side_effect=[True] + [False]*30))
    d = ep.EndPhraseDetector({}, engine=engine)
    monkeypatch.setattr(ep, 'EndPhraseDetector', lambda cfg: d)
    recorder = vm.AudioRecorder()
    recorder._silence_duration = 5
    recorder._silence_threshold = 400
    done = threading.Event()
    callback = Mock(side_effect=done.set)
    recorder.start(callback)
    audio_cb = sd.InputStream.call_args.kwargs['callback']
    for _ in range(8):
        audio_cb(np.full((1600, 1), 250, dtype=np.int16), 1600, None, None)
    assert done.wait(5)
    recorder._endpoint_stop()
    callback.assert_called_once()
    assert recorder.stop_reason == 'end_phrase'
    sd.InputStream.assert_called_once()
    recorder.cancel()
    recorder.shutdown()


def test_hint_once_only_after_successful_prepare(monkeypatch, tmp_path):
    from tools import voice_mode as vm
    hint = tmp_path/'hint.wav'
    hint.touch()
    monkeypatch.setattr(ep, '_hint_played', False)
    monkeypatch.setattr(ep, 'settings', lambda: {'hint_file': str(hint)})
    play = Mock()
    monkeypatch.setattr(vm, 'play_audio_file', play)
    recorder = SimpleNamespace(prepare_endpoint=lambda: True)
    ep.prepare_recording_endpoint(recorder)
    ep.prepare_recording_endpoint(recorder)
    play.assert_called_once_with(str(hint))


def test_shared_transcription_removes_control_phrase(monkeypatch):
    from tools import voice_mode as vm, transcription_tools as stt
    monkeypatch.setattr(ep, 'settings', lambda: {'enabled': True})
    monkeypatch.setattr(stt, 'transcribe_audio', lambda *a, **kw: {'success': True, 'transcript': 'Plan a trip. Over and out.'})
    assert vm.transcribe_recording('unused.wav')['transcript'] == 'Plan a trip.'
