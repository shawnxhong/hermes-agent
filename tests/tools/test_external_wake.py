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
                n = engine.frame_length * 2
                assert len(reader.read(n)) == n
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
