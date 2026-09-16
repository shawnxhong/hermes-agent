"""Ack gates microphone monitoring, not model execution."""
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli.voice_ack import arm_listener_after_ack


def setup_turn():
    q, stop, barrier = queue.Queue(), threading.Event(), threading.Event()
    cli = SimpleNamespace(session_id='test', _voice_mode=True,
                          _voice_continuous=True, _voice_turn_tts_queue=q,
                          _voice_full_duplex_listener=Mock())
    return cli, q, stop, barrier


def test_ack_does_not_hold_up_caller_but_gates_microphone():
    cli, q, stop, barrier = setup_turn()
    worker = arm_listener_after_ack(cli, barrier, q, stop)
    # The caller can start inference while the audio worker is still playing.
    assert worker.is_alive()
    cli._voice_full_duplex_listener.assert_not_called()
    barrier.set()
    worker.join(1)
    assert not worker.is_alive()
    cli._voice_full_duplex_listener.assert_called_once_with()


@pytest.mark.parametrize('change', [
    lambda c, s: s.set(),
    lambda c, s: setattr(c, 'session_id', 'new'),
    lambda c, s: setattr(c, '_scene_generation', 1),
    lambda c, s: setattr(c, '_scene_switching', True),
    lambda c, s: setattr(c, '_voice_turn_tts_queue', queue.Queue()),
    lambda c, s: setattr(c, '_voice_mode', False),
    lambda c, s: setattr(c, '_should_exit', True),
    lambda c, s: setattr(c, '_voice_clarify_listening', True),
    lambda c, s: setattr(c, '_voice_approval_preparing', True),
])
def test_stale_or_cancelled_ack_cannot_reopen_microphone(change):
    cli, q, stop, barrier = setup_turn()
    worker = arm_listener_after_ack(cli, barrier, q, stop)
    change(cli, stop)
    barrier.set()
    worker.join(1)
    assert not worker.is_alive()
    cli._voice_full_duplex_listener.assert_not_called()


def test_disabled_ack_does_not_delay_listener():
    cli, q, stop, _ = setup_turn()
    worker = arm_listener_after_ack(cli, None, q, stop)
    worker.join(1)
    cli._voice_full_duplex_listener.assert_called_once_with()


def test_unfinished_ack_times_out_without_opening_mic(monkeypatch):
    import hermes_cli.voice_ack as ack
    cli, q, stop, _ = setup_turn()
    ticks = iter([0, 31])
    monkeypatch.setattr(ack.time, 'monotonic', lambda: next(ticks))
    barrier = Mock()
    barrier.wait.return_value = False
    worker = arm_listener_after_ack(cli, barrier, q, stop)
    worker.join(1)
    assert not worker.is_alive()
    cli._voice_full_duplex_listener.assert_not_called()
