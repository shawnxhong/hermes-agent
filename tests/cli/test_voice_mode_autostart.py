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
