"""Cancellation must never replay old speech or steal new playback ownership."""
import subprocess
import sys
import threading
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import voice_mode as vm


@pytest.fixture
def audio_rig(tmp_path, monkeypatch):
    path = tmp_path / "speech.mp3"
    path.write_bytes(b"test")
    monkeypatch.setattr(vm, "_active_playback", None)
    monkeypatch.setattr(vm, "_playback_generation", 0)
    monkeypatch.setattr(vm, "_import_audio", lambda: (SimpleNamespace(stop=Mock()), None))
    monkeypatch.setattr(vm.shutil, "which", lambda name: name if name in ("ffplay", "aplay") else None)
    return str(path)


class Player:
    pid = 123

    def __init__(self, on_wait=None, code=0):
        self.returncode = None
        self.on_wait = on_wait
        self.code = code

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        if self.on_wait:
            self.on_wait()
        if self.returncode is None:
            self.returncode = self.code
        return self.returncode


def test_cancel_does_not_fallback(audio_rig, monkeypatch):
    players = []
    def spawn(cmd, **kwargs):
        players.append(cmd[0])
        return Player(on_wait=vm.stop_playback)
    monkeypatch.setattr(vm.subprocess, "Popen", spawn)
    assert vm.play_audio_file(audio_rig)
    assert players == ["ffplay"]
    assert vm._active_playback is None


@pytest.mark.linux_only
def test_genuine_failure_still_uses_fallback(audio_rig, monkeypatch):
    players = []
    def spawn(cmd, **kwargs):
        players.append(cmd[0])
        return Player(code=1 if cmd[0] == "ffplay" else 0)
    monkeypatch.setattr(vm.subprocess, "Popen", spawn)
    assert vm.play_audio_file(audio_rig)
    assert players == ["ffplay", "aplay"]


def test_old_worker_cannot_clear_new_player(audio_rig, monkeypatch):
    new = Player()
    def interrupt_then_announce():
        vm.stop_playback()
        # A new scene announcement registers before the old wait() returns.
        with vm._playback_lock:
            vm._active_playback = new
    monkeypatch.setattr(vm.subprocess, "Popen", lambda *a, **k: Player(interrupt_then_announce))
    assert vm.play_audio_file(audio_rig)
    assert vm._active_playback is new


def test_cancel_during_player_resolution_prevents_spawn(audio_rig, monkeypatch):
    def resolve(name):
        if name == "ffplay":
            vm.stop_playback()
            return name
        return None
    monkeypatch.setattr(vm.shutil, "which", resolve)
    spawn = Mock()
    monkeypatch.setattr(vm.subprocess, "Popen", spawn)
    assert vm.play_audio_file(audio_rig)
    spawn.assert_not_called()


@pytest.mark.parametrize("device_error", [False, True])
def test_cancel_sounddevice_does_not_stop_new_audio_or_fallback(audio_rig, monkeypatch, tmp_path, device_error):
    import numpy as np
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\0\0" * 240)
    sd = SimpleNamespace(play=Mock(), stop=Mock())
    def stream():
        vm.stop_playback()
        if device_error:
            raise OSError("device closed during cancellation")
        return SimpleNamespace(active=False)
    sd.get_stream = stream
    monkeypatch.setattr(vm, "_import_audio", lambda: (sd, np))
    monkeypatch.setattr(vm, "_sounddevice_output_allowed", lambda: True)
    spawn = Mock()
    monkeypatch.setattr(vm.subprocess, "Popen", spawn)
    assert vm.play_audio_file(str(path))
    sd.play.assert_called_once()
    sd.stop.assert_called_once()  # only the cancellation, not stale cleanup
    spawn.assert_not_called()


@pytest.mark.linux_only
def test_real_child_terminated_without_fallback(audio_rig, monkeypatch):
    """Real OS child/wait/terminate; no sound or live Hermes session involved."""
    real_popen = subprocess.Popen
    entered = threading.Event()
    children = []
    results = []
    def spawn(cmd, **kwargs):
        proc = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(proc)
        entered.set()
        return proc
    monkeypatch.setattr(vm.subprocess, "Popen", spawn)
    worker = threading.Thread(target=lambda: results.append(vm.play_audio_file(audio_rig)))
    worker.start()
    try:
        assert entered.wait(5)
        vm.stop_playback()
        worker.join(5)
        assert not worker.is_alive()
        assert len(children) == 1
        assert children[0].returncode == -15
        assert results == [True]
    finally:
        for proc in children:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        worker.join(5)
