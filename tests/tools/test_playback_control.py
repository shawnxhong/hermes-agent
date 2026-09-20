import json
import threading
import time
from unittest.mock import Mock

from tools import playback_control as pc


def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_MEDIA_BRIDGE', str(tmp_path / 'service.sock'))
    (tmp_path / 'service.sock').touch()
    stop = Mock()
    monkeypatch.setattr('tools.voice_mode.stop_playback', stop)
    return stop


def test_epoch_cancels_queue_and_late_result(monkeypatch, tmp_path):
    stop = setup(monkeypatch, tmp_path)
    event = threading.Event()
    with pc.output_guard(event):
        (tmp_path / 'epoch').write_text('new')
        assert event.wait(1)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if all(json.loads(p.read_text())['cancelled'] for p in (tmp_path/'leases').glob('*.json')):
                break
            time.sleep(.01)
        assert stop.called
    played = []
    @pc.cancellable_output
    def play(stop_event=None):
        played.append(True)
    play(stop_event=event)  # stale synthesizer result
    assert not played
    play()  # new confirmation is allowed
    assert played == [True]


def test_stale_guard_does_not_kill_new_announcement(monkeypatch, tmp_path):
    stop = setup(monkeypatch, tmp_path)
    event = threading.Event()
    event._hermes_playback_epoch = 'old'
    with pc.output_guard(event):
        assert event.is_set()
    stop.assert_not_called()


def test_no_bridge_preserves_behavior(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_MEDIA_BRIDGE', str(tmp_path/'absent.sock'))
    with pc.output_guard() as event:
        assert event is None


def test_cancelled_stream_releases_done(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    @pc.cancellable_output
    def stream(stop_event, tts_done_event):
        raise AssertionError('must not generate')
    event, done = threading.Event(), threading.Event()
    event.set()
    stream(event, done)
    assert done.is_set()
