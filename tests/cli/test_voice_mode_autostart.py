"""Regression coverage for the screenless demo's CLI voice bootstrap."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli import HermesCLI


def test_voice_runtime_stays_opt_in_without_launcher_flag(monkeypatch):
    monkeypatch.delenv("HERMES_CLI_VOICE_AUTO_START", raising=False)
    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_mode = False
    cli._enable_voice_mode = Mock()

    assert cli._maybe_auto_enable_voice_mode() is False
    cli._enable_voice_mode.assert_not_called()


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_voice_launcher_flag_enables_runtime_before_first_wake(monkeypatch, value):
    monkeypatch.setenv("HERMES_CLI_VOICE_AUTO_START", value)
    cli = HermesCLI.__new__(HermesCLI)
    cli._voice_mode = False

    def enable():
        cli._voice_mode = True

    cli._enable_voice_mode = Mock(side_effect=enable)

    assert cli._maybe_auto_enable_voice_mode() is True
    cli._enable_voice_mode.assert_called_once_with()


def test_demo_push_to_talk_is_one_shot_but_default_remains_continuous(monkeypatch):
    monkeypatch.delenv("HERMES_CLI_PTT_ONESHOT", raising=False)
    assert HermesCLI._manual_voice_capture_continuous() is True
    monkeypatch.setenv("HERMES_CLI_PTT_ONESHOT", "1")
    assert HermesCLI._manual_voice_capture_continuous() is False


def test_initial_wake_ready_cue_rearms_listener_before_signalling(monkeypatch):
    from tools import voice_mode, wake_word
    import cli as cli_module

    monkeypatch.setenv("HERMES_CLI_WAKE_READY_CUE", "1")
    order = []
    monkeypatch.setattr(
        wake_word, "pause_listening",
        lambda **kwargs: order.append("pause") or True,
    )
    monkeypatch.setattr(
        wake_word, "resume_listening",
        lambda **kwargs: order.append("resume") or True,
    )
    monkeypatch.setattr(
        voice_mode, "play_beep",
        lambda **kwargs: order.append(("beep", kwargs)),
    )
    monkeypatch.setattr(
        cli_module.time, "sleep",
        lambda value: order.append(("sleep", value)),
    )
    cli = SimpleNamespace(_wake_suspended=False)

    assert HermesCLI._prepare_initial_wake_listener(cli) is True
    assert order == [
        "pause",
        ("beep", {"frequency": 1040, "count": 2}),
        ("sleep", 0.25),
        "resume",
    ]
    assert cli._wake_suspended is False
    assert cli._wake_ready_cue_played is True
    assert HermesCLI._prepare_initial_wake_listener(cli) is True
    assert len(order) == 4


@pytest.mark.parametrize(
    ("active", "suspended"),
    [(False, False), (False, True), (True, True)],
)
def test_manual_capture_needs_no_pause_without_active_wake(
    monkeypatch, active, suspended
):
    from tools import wake_word

    pause = Mock()
    monkeypatch.setattr(wake_word, "pause_listening", pause)
    cli = SimpleNamespace(
        _wake_word_active=active,
        _wake_suspended=suspended,
    )

    assert HermesCLI._pause_wake_word_for_manual_capture(cli) is True
    pause.assert_not_called()


def test_manual_capture_pauses_active_wake_listener(monkeypatch):
    from tools import wake_word

    pause = Mock(return_value=True)
    monkeypatch.setattr(wake_word, "pause_listening", pause)
    cli = SimpleNamespace(
        _wake_word_active=True,
        _wake_suspended=False,
    )

    assert HermesCLI._pause_wake_word_for_manual_capture(cli) is True
    assert cli._wake_suspended is True
    pause.assert_called_once_with(owner=cli)
