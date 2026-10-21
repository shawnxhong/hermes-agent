"""Recording-only keyword detection across old SDK segments and real sockets."""
import importlib.util
import json
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from tools import voice_endpoint as ep
from tools.external_wake import ExternalEOSDetector

spec = importlib.util.spec_from_file_location('recording_pcm_service', Path(__file__).resolve().parents[2] / 'demo-runtime/wake/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
EOS = 2


@pytest.fixture
def rig(tmp_path, monkeypatch):
    server = service.Server(logging.getLogger('pcm-test'), ['Hi Intel'], EOS,
                            {'enabled': True, 'phrase': 'thank you'})
    alive = threading.Event(); alive.set()
    errors = []
    def serve():
        try:
            server.serve(Mock(capture_alive=alive.is_set), tmp_path / 'service.sock')
        except RuntimeError:
            if alive.is_set():
                errors.append(True)
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    metadata = tmp_path / 'endpoint-config.json'
    deadline = time.monotonic() + 5
    while not metadata.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert metadata.exists()
    original = ep.EndPhraseDetector
    engines, keywords = [], []
    def make(cfg):
        engine = Mock(process=Mock(side_effect=lambda pcm: bool(np.any(pcm == 12000))))
        detector = original(cfg, engine=engine)
        engines.append(engine); keywords.append(detector)
        return detector
    monkeypatch.setattr(ep, 'EndPhraseDetector', make)
    detector = ExternalEOSDetector({'socket': str(tmp_path / 'service.sock')})
    assert detector.prepare()
    try:
        yield server, detector, engines, metadata
    finally:
        detector.stop()
        for keyword in keywords:
            keyword.close()
        alive.clear(); thread.join(4)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize('early_ms', [24.62, 240.071353, 524.261089])
def test_real_failure_old_unknown_sdk_bos_no_longer_blocks_recorder_phrase(rig, early_ms):
    server, detector, engines, _ = rig
    done = threading.Event(); callback = Mock(side_effect=done.set)
    detector.arm_audio(callback, 16000, 400)
    detector.start(callback, 16000, 400)
    assert server.end_phrase_source == 'recorder'
    old = server.armed_after - early_ms
    server.result(old, 0, b'Question thank you')
    server.result(old, EOS, b'Question thank you')
    assert not done.is_set()  # Neither stale text nor stale EOS can stop us.
    detector.feed(np.full((1280, 1), 12000, dtype=np.int16))
    assert done.wait(5)
    callback.assert_called_once()
    assert detector.last_reason == 'end_phrase'
    engines[0].reset.assert_called_once()


def test_recorder_ownership_ignores_shared_asr_phrase_but_accepts_native_eos(rig):
    server, detector, _, _ = rig
    done = threading.Event(); callback = Mock(side_effect=done.set)
    detector.arm_audio(callback, 16000, 400); detector.start(callback, 16000, 400)
    stamp = server.armed_after + 1
    server.result(stamp, 0, b'Question thank you')
    assert not server.delivered
    server.result(stamp, EOS, b'')
    assert done.wait(5) and detector.last_reason == 'aisp_eos'
    detector.feed(np.full((1280, 1), 12000, dtype=np.int16))
    callback.assert_called_once()


def test_old_callback_and_queued_audio_cannot_end_next_recording(rig):
    _, detector, engines, _ = rig
    callback = Mock()
    detector.arm_audio(callback, 16000, 400)
    old_scope = detector._audio_scope
    detector.feed(np.zeros((100, 1), dtype=np.int16))
    detector.stop()
    detector.arm_audio(callback, 16000, 400)
    detector._finish(old_scope, 'end_phrase')
    callback.assert_not_called()
    assert not detector._audio_scope.is_set()
    assert detector._keyword._pending_samples == 0
    assert engines[0].reset.call_count == 2
    detector._finish(detector._audio_scope, 'end_phrase')
    detector._finish(detector._audio_scope, 'aisp_eos')
    callback.assert_called_once()


def test_keyword_before_native_subscription_completes_is_not_rearmed(rig):
    _, detector, _, _ = rig
    callback = Mock()
    detector.arm_audio(callback, 16000, 400)
    scope = detector._audio_scope
    detector._finish(scope, 'end_phrase')
    detector.start(callback, 16000, 400)
    callback.assert_called_once()
    assert detector.last_reason == 'end_phrase' and detector.sock is None


def test_simultaneous_eos_and_phrase_call_callback_only_once(rig):
    _, detector, _, _ = rig
    callback = Mock(); detector.arm_audio(callback, 16000, 400)
    scope = detector._audio_scope
    barrier = threading.Barrier(3)
    def finish(reason):
        barrier.wait(); detector._finish(scope, reason)
    threads = [threading.Thread(target=finish, args=(reason,)) for reason in ['aisp_eos', 'end_phrase']]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join(3); assert not thread.is_alive()
    callback.assert_called_once()


def test_config_change_rebuilds_keyword_but_repeated_prepare_keeps_warm_model(rig):
    _, detector, engines, metadata = rig
    assert detector.prepare() and len(engines) == 1
    value = json.loads(metadata.read_text())
    value['end_phrase']['phrase'] = 'finish now'; metadata.write_text(json.dumps(value))
    assert detector.prepare() and len(engines) == 2
    assert detector._keyword.phrase == 'finish now'
    value['end_phrase']['enabled'] = False; metadata.write_text(json.dumps(value))
    assert detector.prepare() and detector._keyword is None
    callback = Mock(); detector.arm_audio(callback, 16000, 400)
    detector.feed(np.full((1280, 1), 12000, dtype=np.int16))
    callback.assert_not_called()


def test_failed_reconfiguration_does_not_keep_stale_prepared_metadata(rig, monkeypatch):
    _, detector, _, metadata = rig
    value = json.loads(metadata.read_text()); value['end_phrase']['phrase'] = 'new phrase'
    metadata.write_text(json.dumps(value))
    monkeypatch.setattr(ep, 'EndPhraseDetector', Mock(side_effect=RuntimeError('missing local model')))
    with pytest.raises(RuntimeError): detector.prepare()
    assert detector._prepared is None and detector._keyword is None
    assert 'end_phrase_source' not in detector.subscription()


def test_ready_rejects_stale_server_instance_or_config(rig):
    server, detector, _, _ = rig
    detector.recording_id = 'current'
    base = dict(event='ready', mode='recording', recording_id='current', endpoint='aisp-eos',
                instance=server.instance, end_phrase=server.end_phrase,
                recording_end_phrase_version=2, end_phrase_source='recorder')
    assert detector.ready(base)
    assert not detector.ready({**base, 'instance': 'previous'})
    assert not detector.ready({**base, 'end_phrase': {'enabled': False, 'phrase': 'thank you'}})
    assert not detector.ready({**base, 'end_phrase_source': 'asr'})
    assert not detector.matched(dict(event='end_recording', recording_id='current', reason='end_phrase'))


def test_actual_recorder_trims_pre_cue_pcm_before_keyword_worker(rig, monkeypatch):
    from tools import voice_mode as vm
    _, detector, engines, _ = rig
    sd = Mock()
    monkeypatch.setattr(vm, '_import_audio', lambda: (sd, np))
    monkeypatch.setattr(vm, '_default_input_samplerate', lambda _: 16000)
    monkeypatch.setattr(ep, 'settings', lambda: {'provider': 'aisp-eos'})
    monkeypatch.setattr('tools.external_wake.ExternalEOSDetector', lambda cfg: detector)
    recorder = vm.AudioRecorder(); done = threading.Event()
    callback = Mock(side_effect=done.set)
    recorder.start(callback, capture_after=10.)
    audio_cb = sd.InputStream.call_args.kwargs['callback']
    try:
        audio_cb(np.full((1280, 1), 12000, dtype=np.int16), 1280,
                 SimpleNamespace(inputBufferAdcTime=9.9), None)
        engines[0].process.assert_not_called()
        assert recorder._frames == []
        # Half this block predates the cue. Only its quiet second half survives.
        crossing = np.concatenate([np.full((640, 1), 12000, dtype=np.int16), np.zeros((640, 1), dtype=np.int16)])
        audio_cb(crossing, 1280, SimpleNamespace(inputBufferAdcTime=9.96), None)
        audio_cb(np.zeros((640, 1), dtype=np.int16), 640, SimpleNamespace(inputBufferAdcTime=10.04), None)
        audio_cb(np.full((1280, 1), 12000, dtype=np.int16), 1280, SimpleNamespace(inputBufferAdcTime=10.08), None)
        assert done.wait(5)
        assert recorder.stop_reason == 'end_phrase'
        callback.assert_called_once()
        assert np.all(engines[0].process.call_args_list[0].args[0] == 0)
        sd.InputStream.assert_called_once()
    finally:
        recorder.cancel(); recorder.shutdown()


def test_failed_fresh_keyword_stream_reset_is_visible():
    from tools.wake_word import _SherpaKwsEngine
    engine = object.__new__(_SherpaKwsEngine)
    engine._spotter = Mock(create_stream=Mock(side_effect=RuntimeError('failed reset')))
    with pytest.raises(RuntimeError, match='failed reset'): engine.reset()


def test_native_socket_failure_does_not_disable_current_recording_keyword(rig):
    server, detector, _, _ = rig
    detector.path += '.missing'
    done = threading.Event(); detector.arm_audio(done.set, 16000, 400)
    with pytest.raises(OSError): detector.start(done.set, 16000, 400)
    detector.feed(np.full((1280, 1), 12000, dtype=np.int16))
    assert done.wait(5) and detector.last_reason == 'end_phrase'


def test_reset_failure_falls_back_to_native_instead_of_breaking_capture(rig):
    server, detector, engines, _ = rig
    engines[0].reset.side_effect = RuntimeError('failed reset')
    callback = Mock(); detector.arm_audio(callback, 16000, 400)
    assert detector._keyword is None and detector._prepared is None
    detector.start(callback, 16000, 400)
    server.result(server.armed_after + 1, EOS, b'Current question')
    deadline = time.monotonic() + 5
    while not callback.called and time.monotonic() < deadline: time.sleep(.01)
    callback.assert_called_once()


@pytest.mark.parametrize('invalid', [None, '{', '{}'])
def test_missing_or_invalid_metadata_discards_previous_keyword_config(rig, invalid):
    _, detector, _, metadata = rig
    if invalid is None:
        metadata.unlink()
        assert detector.prepare() is False
    else:
        metadata.write_text(invalid)
        with pytest.raises((ValueError, TypeError)): detector.prepare()
    assert detector._prepared is None and detector._keyword is None


def test_cli_warms_once_without_opening_microphone(monkeypatch):
    from tools import voice_mode as vm
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {'wake_word': {'enabled': True}})
    monkeypatch.setattr(ep, 'settings', lambda: {'provider': 'aisp-eos'})
    recorder = Mock(); factory = Mock(return_value=recorder)
    monkeypatch.setattr(vm, 'create_audio_recorder', factory)
    cli = SimpleNamespace(_voice_recorder=None)
    ep.warm_for_cli(cli); ep.warm_for_cli(cli)
    factory.assert_called_once()
    assert recorder.prepare_endpoint.call_count == 2
    recorder.prepare_capture.assert_not_called()
    recorder.start.assert_not_called()
