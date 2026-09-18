import threading
import time
import wave

import numpy as np
import pytest

from tools.wake_asr import ASRWakeEngine, matches_phrase


@pytest.mark.parametrize('text,expected', [
    ('HELLO, Intel!', True), ('well hello intel please', True),
    ('hello intelligent', False), ('say hi inter', True),
    ('the weather is nice', False), ('', False), ('Intel', False),
])
def test_phrase_boundaries(text, expected):
    assert matches_phrase(text, ['Hello Intel', 'Hi Inter']) is expected


def utterance(engine):
    for _ in range(5):
        engine.process(np.full(1280, 1000, dtype=np.int16))
    for _ in range(5):
        engine.process(np.zeros(1280, dtype=np.int16))


def await_result(engine):
    deadline = time.monotonic() + 5
    while engine._busy.is_set() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not engine._busy.is_set()
    return engine.process(np.zeros(1280, dtype=np.int16))


def test_audio_roundtrip_and_one_shot():
    calls = []
    def transcribe(path, language):
        with wave.open(path) as wav:
            calls.append((wav.getframerate(), wav.getnchannels(), language))
        return {'success': True, 'transcript': 'Hello, Intel.'}
    engine = ASRWakeEngine({'phrase': 'Hello Intel'}, transcribe=transcribe)
    utterance(engine)
    assert await_result(engine)
    assert calls == [(16000, 1, 'en')]
    assert not engine.process(np.zeros(1280, dtype=np.int16))


@pytest.mark.parametrize('action', ['reset', 'close'])
def test_stale_inference_and_no_backlog(action):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def transcribe(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {'success': True, 'transcript': 'Hello Intel'}
    engine = ASRWakeEngine({'phrase': 'Hello Intel'}, transcribe=transcribe)
    try:
        utterance(engine)
        assert entered.wait(5)
        for _ in range(20):
            utterance(engine)
        assert len(calls) == 1
        getattr(engine, action)()
    finally:
        release.set()
    assert not await_result(engine)


def test_silence_is_not_transcribed():
    def forbidden(*a, **k):
        raise AssertionError('silence reached ASR')
    engine = ASRWakeEngine({}, transcribe=forbidden)
    for _ in range(1000):
        assert not engine.process(np.zeros(1280, dtype=np.int16))
    assert not engine._busy.is_set()
    assert len(engine._pre) <= 3


def test_failure_rearms():
    calls = []
    def transcribe(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError()
        return {'success': True, 'transcript': 'Hello Intel'}
    engine = ASRWakeEngine({'phrase': 'Hello Intel'}, transcribe=transcribe)
    utterance(engine)
    assert not await_result(engine)
    utterance(engine)
    assert await_result(engine)
