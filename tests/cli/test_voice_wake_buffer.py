"""Wake replay timing and the real sidecar/client/TTS seams; no real audio."""
from contextlib import nullcontext
import importlib.util
import json
import logging
from pathlib import Path
import queue
import socket
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from hermes_cli import voice_wake_buffer as buffer
from tools.external_wake import ExternalWakeEngine

spec = importlib.util.spec_from_file_location(
    "wake_buffer_service", Path(__file__).resolve().parents[2] / "demo-runtime/wake/service.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


@pytest.fixture
def cli(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(config, "load_config", lambda: {
        "voice": {"wake_buffer_seconds": 1},
        "wake_word": {"provider": "external-asr"}})
    obj = NS(session_id="reply", _scene_generation=0, _voice_mode=True,
             _voice_tts=True, _wake_word_active=True, _voice_continuous=False,
             _agent_running=False, _voice_recording=False, _voice_processing=False,
             _pending_input=queue.Queue(), _voice_tts_done=threading.Event(),
             _voice_last_tts_text="Your request is complete.")
    return obj


def ready_buffer(cli):
    state = buffer.begin(cli, threading.Event())
    assert state is not None
    state.complete(time.monotonic() * 1000)
    cli._voice_tts_done.set()
    return state


def test_waits_for_audio_and_agent_then_consumes_once(cli):
    state = buffer.begin(cli, threading.Event())
    assert buffer.take_replay(cli) is None
    state.complete(time.monotonic() * 1000)
    assert buffer.take_replay(cli) is None
    cli._voice_tts_done.set()
    cli._agent_running = True
    assert buffer.take_replay(cli) is None
    cli._agent_running = False
    request = buffer.take_replay(cli)
    assert 0 <= request["end_ms"] - request["start_ms"] <= 1000
    assert request["turn"] == state.turn
    assert buffer.take_replay(cli) is None
    assert not state.stop.is_set()  # Replay never interrupts playback.


@pytest.mark.parametrize("change", ["session", "scene", "cancel", "typed", "exit", "voice_off", "wake_off"])
def test_invalidated_reply_never_replays(cli, change):
    state = ready_buffer(cli)
    if change == "session": cli.session_id = "different"
    elif change == "scene": cli._scene_generation += 1
    elif change == "cancel": state.stop.set()
    elif change == "typed": cli._pending_input.put("typed task")
    elif change == "exit": cli._should_exit = True
    elif change == "voice_off": cli._voice_mode = False
    else: cli._wake_word_active = False
    assert buffer.take_replay(cli) is None
    assert state.consumed


@pytest.mark.parametrize("seconds", [0, -1, 3, True, "1", float("nan")])
def test_disabled_or_invalid_settings_do_not_create_buffer(cli, monkeypatch, seconds):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {
        "voice": {"wake_buffer_seconds": seconds}, "wake_word": {"provider": "external-asr"}})
    assert buffer.begin(cli, threading.Event()) is None


def test_new_reply_replaces_old_completion(cli):
    first = buffer.begin(cli, threading.Event())
    second = buffer.begin(cli, threading.Event())
    first.complete(time.monotonic() * 1000)
    assert first.finished_ms is None and second.finished_ms is None


def test_expired_completion_is_dropped(cli, monkeypatch):
    state = ready_buffer(cli)
    monkeypatch.setattr(buffer, "time", NS(monotonic=lambda: state.finished_ms / 1000 + 6))
    assert buffer.take_replay(cli) is None


@pytest.fixture
def rig(monkeypatch):
    now = [9.8]
    monkeypatch.setattr(service, "time", NS(monotonic=lambda: now[0]))
    s = service.Server(logging.getLogger("wake-buffer-test"), ["Hello Intel"], 2)
    return s, now


def arm_replay(server, **window):
    conn = Mock()
    server.arm(conn, "wake", None, 10050)
    server.replay_wake({"start_ms": 9000, "end_ms": 10000, "turn": "test", **window})
    return conn


@pytest.mark.parametrize("received,accepted", [(9.8, True), (9.1, True), (9.0, True), (8.9, False), (10.1, False)])
def test_last_second_boundary_uses_first_recognition_not_segment_start(rig, received, accepted):
    s, now = rig
    now[0] = received
    s.result(1, 0, b"Hello Intel")  # SDK sentence can predate the rearm.
    conn = arm_replay(s)
    assert bool(conn.sendall.called) == accepted
    if accepted:
        msg = json.loads(conn.sendall.call_args.args[0])
        assert msg["buffered"] and msg["event"] == "wake"
        assert s.delivered


def test_repeated_partials_cannot_refresh_expired_keyword(rig):
    s, now = rig
    now[0] = 8.9
    s.result(1, 0, b"Hello Intel")
    now[0] = 9.9
    s.result(1, 0, b"Hello Intel again")
    arm_replay(s).sendall.assert_not_called()


def test_replay_is_once_even_if_requested_twice(rig):
    s, _ = rig
    s.result(1, 0, b"Hello Intel")
    s.result(2, 0, b"Hello Intel")
    conn = arm_replay(s)
    s.replay_wake({"start_ms": 9000, "end_ms": 10000})
    assert conn.sendall.call_count == 1


def test_tts_own_wake_phrase_is_not_replayed(rig):
    s, _ = rig
    s.result(1, 0, b"Hello Intel")
    arm_replay(s, echo_text="You can say Hello Intel to wake me.").sendall.assert_not_called()


def test_keyword_inside_active_recording_is_not_a_cached_wake(rig):
    s, _ = rig
    s.arm(Mock(), "recording", "current", 0)
    s.result(1, 0, b"Explain Hello Intel please")
    s.conn = None
    arm_replay(s).sendall.assert_not_called()


def test_recording_eos_never_replays_old_wake(rig):
    s, _ = rig
    s.result(1, 0, b"Hello Intel")
    conn = Mock()
    s.arm(conn, "recording", "new", 10050)
    s.replay_wake({"start_ms": 9000, "end_ms": 10000})
    s.result(1, 2, b"Hello Intel")
    conn.sendall.assert_not_called()


@pytest.mark.parametrize("window", [
    {"start_ms": float("nan")}, {"end_ms": True},
    {"start_ms": 10001}, {"start_ms": 1}, {"end_ms": 11000}])
def test_malformed_or_unbounded_replay_is_ignored(rig, window):
    s, _ = rig
    s.result(1, 0, b"Hello Intel")
    arm_replay(s, **window).sendall.assert_not_called()


def test_real_socket_ready_precedes_replay_after_tts_completion(cli, tmp_path):
    s = service.Server(logging.getLogger("wake-buffer-socket"), ["Hello Intel"], 2)
    running = threading.Event(); running.set()
    path = tmp_path / "wake.sock"
    def serve():
        try: s.serve(NS(capture_alive=running.is_set), path)
        except RuntimeError:
            assert not running.is_set()
    worker = threading.Thread(target=serve, daemon=True); worker.start()
    deadline = time.monotonic() + 3
    while not path.exists() and time.monotonic() < deadline: time.sleep(.01)
    engine = ExternalWakeEngine({"external": {"socket": str(path)}})
    try:
        state = buffer.begin(cli, threading.Event())
        s.result(1, 0, b"Hello Intel")
        assert not state.stop.is_set() and buffer.take_replay(cli) is None
        state.complete(time.monotonic() * 1000)
        assert buffer.take_replay(cli) is None
        cli._voice_tts_done.set()
        engine.set_replay(buffer.take_replay(cli))
        engine.reset()  # Must read ready, not accidentally consume wake.
        deadline = time.monotonic() + 3
        matched = False
        while not matched and time.monotonic() < deadline:
            matched = engine.process(None)
            if not matched: time.sleep(.01)
        assert matched and engine.last_match[0] == "Hello Intel"
        assert not state.stop.is_set()
        engine.reset()
        assert engine.replay is None and not engine.process(None)
    finally:
        engine.close(); running.clear(); worker.join(4)
    assert not worker.is_alive()


@pytest.mark.parametrize("cancel,played", [(False, True), (True, True), (False, False)])
def test_native_multi_sentence_tts_reports_only_after_final_audio(cli, monkeypatch, cancel, played):
    from tools import tts_tool, tts_streaming, voice_mode, playback_control
    monkeypatch.setattr(tts_streaming, "resolve_streaming_provider", lambda *a, **k: None)
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "test"})
    monkeypatch.setattr("hermes_cli.voice_wake_ack.cached_turn_ack", lambda *a: None)
    monkeypatch.setattr(playback_control, "output_guard", lambda *a: nullcontext())
    def synth(text, output_path):
        Path(output_path).write_bytes(b"fixture")
        return json.dumps({"success": True, "file_path": output_path})
    monkeypatch.setattr(tts_tool, "text_to_speech_tool", synth)
    stop = threading.Event(); done = cli._voice_tts_done
    state = buffer.begin(cli, stop)
    calls = []
    def play(path):
        assert not done.is_set() and state.finished_ms is None
        assert buffer.take_replay(cli) is None
        calls.append(path)
        if cancel: stop.set()
        return played
    monkeypatch.setattr(voice_mode, "play_audio_file", play)
    completed = []
    def finish(when):
        assert len(calls) == 2 and not done.is_set()
        completed.append(when); state.complete(when)
    q = queue.Queue()
    q.put(tts_tool.ImmediateTTSUtterance("First sentence."))
    q.put(tts_tool.ImmediateTTSUtterance("Second sentence.")); q.put(None)
    tts_tool.stream_tts_to_speaker(q, stop, done, playback_finished_callback=finish)
    assert done.is_set()
    if cancel or not played:
        assert not completed and buffer.take_replay(cli) is None
    else:
        assert len(completed) == 1 and buffer.take_replay(cli) is not None


def test_watchdog_replays_on_first_idle_poll_after_completed_tts(cli, monkeypatch):
    from cli import HermesCLI
    import tools.wake_word as wake
    ready_buffer(cli)
    cli._wake_suspended = True
    cli._should_exit = False
    resumed = threading.Event()
    def resume(**kwargs):
        assert kwargs["owner"] is cli and kwargs["replay"]["turn"]
        cli._should_exit = True; resumed.set()
        return True
    mock = Mock(side_effect=resume)
    monkeypatch.setattr(wake, "resume_listening", mock)
    HermesCLI._start_wake_watchdog(cli)
    assert resumed.wait(3)
    deadline = time.monotonic() + 3
    while cli._wake_watchdog_started and time.monotonic() < deadline: time.sleep(.01)
    assert not cli._wake_watchdog_started and not cli._wake_suspended
    mock.assert_called_once()


@pytest.mark.parametrize("received", [10.001, 10.025, 10.05])
def test_complete_keyword_in_post_tts_subscription_gap_is_delivered(rig, received):
    s, now = rig
    now[0] = received
    s.result(9950, 0, b"Hello Intel")
    conn = arm_replay(s)
    msg = json.loads(conn.sendall.call_args.args[0])
    assert msg["buffered"] and msg["reason"] == "wake_phrase"
    assert s.delivered


@pytest.mark.parametrize("before", [None, b"Hello", b"Old wording"])
def test_first_complete_keyword_after_rearm_can_finish_old_segment(rig, before):
    s, now = rig
    if before is not None:
        now[0] = 9.95
        s.result(9950, 0, before)
    conn = arm_replay(s)
    assert not conn.sendall.called
    now[0] = 10.2
    # This non-EOS partial may span the arm boundary or revise its prefix.
    s.result(9950, 0, b"Hello Intel")
    assert json.loads(conn.sendall.call_args.args[0])["buffered"]
    s.result(9950, 2, b"Hello Intel")
    assert conn.sendall.call_count == 1


def test_old_segment_without_valid_reply_handoff_is_still_rejected(rig):
    s, now = rig
    conn = Mock()
    s.arm(conn, "wake", None, 10050)
    now[0] = 10.2
    s.result(9950, 0, b"Hello Intel")
    conn.sendall.assert_not_called()


def test_decode_allowance_expires_without_extending_first_recognition(rig):
    s, now = rig
    conn = arm_replay(s)
    now[0] = 15.001
    s.result(9950, 0, b"Hello Intel")
    conn.sendall.assert_not_called()
    now[0] = 15.2
    s.result(9950, 0, b"Hello Intel again")
    conn.sendall.assert_not_called()
    # Ordinary new segments remain live; no wait is added to recognition.
    s.result(15100, 0, b"Hello Intel")
    assert conn.sendall.call_count == 1
    assert not json.loads(conn.sendall.call_args.args[0]).get("buffered")


def test_late_echo_and_repeated_partials_never_self_wake(rig):
    s, now = rig
    conn = arm_replay(s, echo_text="Say Hello Intel after my reply.")
    now[0] = 10.2
    s.result(10100, 0, b"Hello Intel")
    now[0] = 10.5
    s.result(10100, 0, b"Hello Intel again")
    now[0] = 15.5
    s.result(10100, 0, b"Hello Intel again")
    conn.sendall.assert_not_called()
    # A new utterance is not globally disabled by the rejected segment.
    s.result(15501, 0, b"Hello Intel")
    assert conn.sendall.call_count == 1


def test_expired_cached_keyword_cannot_refresh_after_rearm(rig):
    s, now = rig
    now[0] = 8.9
    s.result(1, 0, b"Hello Intel")
    conn = arm_replay(s)
    now[0] = 10.2
    s.result(1, 0, b"Hello Intel again")
    conn.sendall.assert_not_called()


def test_reply_handoff_does_not_loosen_recording_eos_or_end_phrase(rig):
    s, now = rig
    arm_replay(s)
    conn = Mock()
    s.arm(conn, "recording", "next", 10100)
    assert s.wake_handoff is None
    now[0] = 10.2
    s.result(9950, 2, b"Hello Intel thank you")
    conn.sendall.assert_not_called()


@pytest.mark.parametrize("prefix", [None, b"Hello"])
def test_real_socket_late_full_partial_after_ready_is_delivered_once(cli, tmp_path, prefix):
    s = service.Server(logging.getLogger("wake-handoff-socket"), ["Hello Intel"], 2)
    running = threading.Event(); running.set()
    path = tmp_path / "handoff.sock"
    def serve():
        try:
            s.serve(NS(capture_alive=running.is_set), path)
        except RuntimeError:
            assert not running.is_set()
    worker = threading.Thread(target=serve, daemon=True); worker.start()
    deadline = time.monotonic() + 3
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    engine = ExternalWakeEngine({"external": {"socket": str(path)}})
    try:
        state = buffer.begin(cli, threading.Event())
        if prefix is not None:
            s.result(1, 0, prefix)
        state.complete(time.monotonic() * 1000)
        cli._voice_tts_done.set()
        engine.set_replay(buffer.take_replay(cli))
        engine.reset()
        # Ready arrives before the server's locked replay setup; synchronize
        # on that setup, not a fixed sleep that races a loaded test machine.
        deadline = time.monotonic() + 3
        while s.wake_handoff is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert s.wake_handoff is not None
        s.result(1, 0, b"Hello Intel")
        deadline = time.monotonic() + 3
        matched = False
        while not matched and time.monotonic() < deadline:
            matched = engine.process(None)
            if not matched:
                time.sleep(.01)
        assert matched and engine.last_match[0] == "Hello Intel"
        assert s.delivered and not state.stop.is_set()
        s.result(1, 2, b"Hello Intel")
        assert not engine.process(None)
    finally:
        engine.close(); running.clear(); worker.join(4)
    assert not worker.is_alive()
