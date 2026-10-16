"""Behavior of native EOS through the recording-scoped service/socket boundary."""
import importlib.util
import json
import logging
from pathlib import Path
import socket
import threading
import time
from unittest.mock import Mock

import pytest
from tools.external_wake import ExternalEOSDetector, ExternalWakeEngine
from tools import voice_endpoint as endpoint

spec = importlib.util.spec_from_file_location('aisp_eos_service', Path(__file__).resolve().parents[2] / 'demo-runtime/wake/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
EOS = 1 << 1  # Installed SDK AISP_FLAG_EOS; production reads the native constant.


def rig():
    server = service.Server(logging.getLogger('test-eos'), ['Hello Intel'], EOS)
    conn = Mock()
    server.arm(conn, 'recording', 'current', 100)
    return server, conn


def events(conn):
    return [json.loads(call.args[0]) for call in conn.sendall.call_args_list]


@pytest.mark.parametrize('flag', [EOS, EOS | 1, EOS | 4])
def test_final_speech_ends_on_eos_bits(flag):
    server, conn = rig()
    server.result(101, flag, b'What is the weather?')
    assert events(conn) == [{'event': 'end_recording', 'alias': None,
        'timestamp': 101, 'recording_id': 'current', 'reason': 'aisp_eos'}]


def test_keyword_in_partial_does_not_finish_and_empty_eos_does():
    server, conn = rig()
    server.result(101, 0, b'Please explain thank you in French')
    conn.sendall.assert_not_called()
    server.result(101, EOS, b'')
    server.result(101, EOS, b'')
    assert len(events(conn)) == 1


def test_silence_eos_without_speech_is_ignored():
    server, conn = rig()
    server.result(101, EOS, b'')
    conn.sendall.assert_not_called()


def test_old_wake_segment_and_other_segment_eos_are_ignored():
    server, conn = rig()
    server.result(99, EOS, b'Hello Intel')
    server.result(101, 0, b'Current question')
    server.result(102, EOS, b'')
    conn.sendall.assert_not_called()
    server.result(101, EOS, b'')
    assert len(events(conn)) == 1


def test_rearming_drops_prior_recording_speech_and_delivery_state():
    server, conn = rig()
    server.result(101, 0, b'Old question')
    server.arm(conn, 'recording', 'next', 200)
    server.result(101, EOS, b'Old final')
    conn.sendall.assert_not_called()
    server.result(201, EOS, b'New question')
    assert events(conn)[0]['recording_id'] == 'next'


def test_no_subscription_ignores_eos():
    server, conn = rig()
    server.conn = None
    server.result(101, EOS, b'Question')
    conn.sendall.assert_not_called()


def test_wake_phrase_matching_still_works_without_eos():
    server, conn = rig()
    server.arm(conn, 'wake', None, 100)
    server.result(101, 0, b'Hello Intel')
    assert events(conn)[0]['event'] == 'wake'
    server.result(101, EOS, b'Hello Intel')
    assert len(events(conn)) == 1


def test_client_requires_eos_support_and_current_identity():
    detector = ExternalEOSDetector({})
    detector.recording_id = 'current'
    ready = {'event': 'ready', 'mode': 'recording', 'recording_id': 'current'}
    assert not detector.ready(ready)
    assert detector.ready({**ready, 'endpoint': 'aisp-eos'})
    done = {'event': 'end_recording', 'recording_id': 'current'}
    assert not detector.matched(done)
    assert detector.matched({**done, 'reason': 'aisp_eos'})
    assert not detector.matched({**done, 'reason': 'aisp_eos', 'recording_id': 'old'})


def test_eos_settings_override_legacy_keyword_and_preserve_transcript(monkeypatch):
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: {
        'voice': {'endpoint': {'enabled': True, 'provider': 'aisp-eos'},
                  'end_phrase': {'enabled': True, 'phrase': 'thank you'}},
        'wake_word': {'external': {'socket': '/test/socket'}}})
    assert endpoint.settings()['provider'] == 'aisp-eos'
    assert endpoint.settings()['socket'] == '/test/socket'
    for text in ['Please help, thank you.', "Explain that is all", 'Over and out.']:
        assert endpoint.strip_end_phrase(text) == text
    assert endpoint.pending_hint_text() is None


@pytest.fixture
def socket_service(tmp_path):
    server = service.Server(logging.getLogger('test-socket'), ['Hello Intel'], EOS)
    running = threading.Event()
    running.set()
    native = Mock(capture_alive=running.is_set)
    path = tmp_path / 'service.sock'
    errors = []
    def serve():
        try:
            server.serve(native, path)
        except RuntimeError:
            if running.is_set():
                errors.append('unexpected service failure')
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert path.exists()
    yield server, path
    running.clear()
    thread.join(4)
    assert not thread.is_alive()
    assert not errors


def test_client_subscription_retains_canonical_intel_intel_wake_alias(socket_service):
    server, path = socket_service
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(path))
        client.sendall(b'{"mode":"wake","aliases":["Hello Intel"]}\n')
        reader = client.makefile('rb')
        assert json.loads(reader.readline())['event'] == 'ready'
        server.result(server.armed_after + 1, 0, b'Intel Intel')
        event = json.loads(reader.readline())
        assert event['event'] == 'wake' and event['alias'] == 'Intel Intel'


def test_real_socket_eos_callback_once_and_next_recording(socket_service):
    server, path = socket_service
    detector = ExternalEOSDetector({'socket': str(path)})
    done = threading.Event()
    callback = Mock(side_effect=done.set)
    try:
        detector.start(callback, 16000, 500)
        stamp = server.armed_after + 1
        server.result(stamp, 0, b'Question containing thank you')
        callback.assert_not_called()
        server.result(stamp, EOS, b'')
        assert done.wait(5)
        assert detector.last_reason == 'aisp_eos'
        server.result(stamp, EOS, b'')
        callback.assert_called_once()
        detector.stop()
        done.clear()
        detector.start(callback, 16000, 500)
        server.result(stamp, EOS, b'Old question')
        assert callback.call_count == 1
        server.result(server.armed_after + 1, EOS, b'Another question')
        assert done.wait(5)
        assert callback.call_count == 2
    finally:
        detector.stop()


def test_cancelled_client_never_delivers_late_eos(socket_service):
    server, path = socket_service
    detector = ExternalEOSDetector({'socket': str(path)})
    callback = Mock()
    detector.start(callback, 16000, 500)
    detector.stop()
    server.result(server.armed_after + 1, EOS, b'Late result')
    callback.assert_not_called()


def test_recorder_propagates_eos_reason_and_stops_once(monkeypatch):
    from tools import voice_mode
    recorder = voice_mode.AudioRecorder()
    callback = Mock()
    done = threading.Event()
    callback.side_effect = done.set
    recorder._recording = True
    recorder._on_silence_stop = callback
    recorder._end_phrase_detector = Mock(last_reason='aisp_eos')
    recorder._endpoint_stop()
    assert done.wait(5)
    recorder._endpoint_stop()
    callback.assert_called_once()
    assert recorder.stop_reason == 'aisp_eos'
