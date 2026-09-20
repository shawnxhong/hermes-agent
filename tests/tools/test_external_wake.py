import importlib.util
import json
import socket
import threading
from pathlib import Path

import numpy as np
import pytest
from tools.external_wake import ExternalWakeEngine
from tools.wake_word import _build_engine

spec = importlib.util.spec_from_file_location("wake_service", Path(__file__).resolve().parents[2] / "scripts/local-ovms/aisp-wake/service.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


@pytest.mark.parametrize("text", ["Hello Intel", "HELLO, INTEL!", "hello   intel.", "Hi Inter", "hey indel"])
def test_partial_triggers_without_eos(text):
    gate = service.WakeGate(["Hello Intel", "Hi Inter", "Hey Indel"])
    assert gate.result(12, text)[0] == "trigger"
    assert gate.result(12, text + " again")[0] == "same_sentence"
    assert gate.result(13, text)[0] == "inactive"


def test_new_listen_and_negative():
    gate = service.WakeGate(["Hello Intel"])
    assert gate.result(1, "Hello intelligent friend")[0] == "miss"
    assert gate.result(1, "thank you")[0] == "miss"
    assert gate.result(1, "hello intel")[0] == "trigger"
    assert service.WakeGate(["Hello Intel"]).result(1, "hello intel")[0] == "trigger"


def test_socket_path_pause_and_real_factory(tmp_path):
    path = str(tmp_path / "wake.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    cfg = {"provider": "external-asr", "phrase": "Hello Intel", "sherpa": {"aliases": ["High Intel", "Hi Inter"]}, "external": {"socket": path}}
    engine = _build_engine(cfg)
    assert isinstance(engine, ExternalWakeEngine)
    done = threading.Event()
    errors = []
    def serve():
        try:
            conn, _ = listener.accept()
            with conn, conn.makefile("rb") as reader:
                assert json.loads(reader.readline())["aliases"] == ["Hello Intel", "High Intel", "Hi Inter"]
                conn.sendall(b'{"event":"ready"}\n')
                conn.sendall(b'{"event":"wake","alias":"Hi Inter","timestamp":17}\n')
                done.set()
                while reader.read(16000):
                    pass
        except BaseException as e:
            errors.append(e)
            done.set()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        engine.reset()
        frame = np.zeros(engine.frame_length, dtype=np.int16)
        initial = engine.process(frame)
        assert done.wait(5)
        assert initial or engine.process(frame)
        assert engine.last_match == ("Hi Inter", "")
        assert not engine.process(frame)
        engine.pause()
        t.join(5)
        assert not t.is_alive()
        assert not errors
    finally:
        engine.close()
        listener.close()


def test_missing_service_fails_explicitly(tmp_path):
    engine = ExternalWakeEngine({"external": {"socket": str(tmp_path / "missing")}})
    with pytest.raises(OSError):
        engine.reset()


def test_every_callback_logged_and_late_results_do_not_dispatch():
    from unittest.mock import Mock
    log = Mock()
    server = service.Server(log, ["Hello Intel"])
    server.conn = Mock()
    server.armed_after = 0
    server.epoch = "first-listen"
    server.result(4, 1, b"hello")
    server.result(4, 0, b"Hello, Intel!")
    server.result(4, 2, b"Hello Intel.")
    server.result(5, 0, b"Hello Intel")
    records = [call.args[1] for call in log.info.call_args_list]
    assert records == ["hello", "Hello, Intel!", "Hello Intel.", "Hello Intel"]
    server.conn.sendall.assert_called_once()
    # Capture/results continue after subscriber disconnect, without queuing wakes.
    server.conn = None
    server.result(6, 0, b"Hi there, still listening.")
    assert log.info.call_args.args[1] == "Hi there, still listening."
    server.conn = Mock()
    server.delivered = False
    server.armed_after = 100
    server.result(7, 0, b"Hello Intel")
    server.conn.sendall.assert_not_called()
    server.result(101, 0, b"Hello Intel")
    server.conn.sendall.assert_called_once()


def test_continuous_gate_checks_new_sentences_after_trigger():
    gate = service.WakeGate(["Hi Intel"], continuous=True)
    assert gate.result(1, "Hi Intel")[0] == "trigger"
    assert gate.result(1, "Hi Intel again")[0] == "same_sentence"
    assert gate.result(2, "Hi Intel")[0] == "trigger"


def test_service_capture_detector_does_not_open_cli_microphone(monkeypatch):
    import tools.wake_word as wake
    from unittest.mock import Mock
    microphone = Mock(side_effect=AssertionError("CLI must not own wake microphone"))
    monkeypatch.setattr(wake, "_import_audio", microphone)
    engine = Mock(service_capture=True, frame_length=1280)
    engine.process.return_value = False
    detector = wake.WakeWordDetector(engine, lambda: None)
    detector.start()
    detector.pause()
    detector.resume()
    detector.stop()
    microphone.assert_not_called()
    assert engine.reset.call_count == 2


def test_recording_mode_only_delivers_end_for_current_epoch():
    from unittest.mock import Mock
    server = service.Server(Mock(), ["Hello Intel"])
    server.mode, server.recording_id = "recording", "current"
    server.conn = Mock()
    server.armed_after = 100
    server.result(90, 0, b"thank you")  # old sentence
    server.result(101, 0, b"Hello Intel")  # wake disabled during recording
    server.conn.sendall.assert_not_called()
    server.result(102, 0, b"Thank you.")
    server.result(102, 2, b"Thank you!")
    server.result(103, 0, b"thank you")
    server.conn.sendall.assert_called_once()
    event = json.loads(server.conn.sendall.call_args.args[0])
    assert event['event'] == 'end_recording'
    assert event['recording_id'] == 'current'


def test_idle_thank_you_does_not_wake_and_word_boundaries():
    from unittest.mock import Mock
    server = service.Server(Mock(), ["Hello Intel"])
    server.conn = Mock()
    server.armed_after = 0
    server.result(1, 0, b"thank you")
    server.conn.sendall.assert_not_called()
    assert service.WakeGate(['thank you']).result(2,b'thank yourself'.decode())[0] == 'miss'


def test_real_socket_recording_subscription_cancel_and_stale_id(tmp_path):
    import time
    from unittest.mock import Mock
    from tools.external_wake import ExternalEndPhraseDetector
    path = tmp_path/'s'
    server = service.Server(Mock(), ['Hello Intel'])
    running = threading.Event(); running.set()
    def serve():
        try:
            server.serve(Mock(capture_alive=running.is_set), path)
        except RuntimeError:
            pass
    t = threading.Thread(target=serve, daemon=True); t.start()
    until = time.monotonic()+5
    while not path.exists() and time.monotonic()<until:
        time.sleep(.01)
    detector = ExternalEndPhraseDetector({'socket':str(path)})
    callback = Mock()
    done = threading.Event()
    try:
        detector.start(callback,16000,400)
        old_id = detector.recording_id
        detector.stop()
        detector.start(lambda:(callback(),done.set()),16000,400)
        assert detector.recording_id != old_id
        with server.lock:
            server.conn.sendall((json.dumps({'event':'end_recording','recording_id':old_id})+'\n').encode())
        server.result(time.monotonic()*1000+1,0,b'Thank you.')
        assert done.wait(5)
        detector.stop()
        callback.assert_called_once()
        assert detector.last_reason == 'end_phrase'
    finally:
        detector.stop(); running.clear(); t.join(3)


def test_external_recorder_arms_after_stream_and_disarms_on_cancel(monkeypatch):
    from unittest.mock import Mock
    from tools import voice_mode as vm, voice_endpoint as ep, external_wake as ew
    sd = Mock()
    monkeypatch.setattr(vm,'_import_audio',lambda:(sd,np))
    monkeypatch.setattr(vm,'_default_input_samplerate',lambda _:16000)
    monkeypatch.setattr(ep,'settings',lambda:{'enabled':True,'provider':'external-asr'})
    monkeypatch.setattr(ep,'EndPhraseDetector',Mock(side_effect=AssertionError('Sherpa must not start')))
    d = Mock()
    monkeypatch.setattr(ew,'ExternalEndPhraseDetector',lambda cfg:d)
    recorder = vm.AudioRecorder()
    def armed(*args):
        assert recorder._recording
        sd.InputStream.return_value.start.assert_called_once()
    d.start.side_effect = armed
    recorder.start(Mock())
    recorder.cancel()
    d.stop.assert_called()
    recorder.shutdown()
