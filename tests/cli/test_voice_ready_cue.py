from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hermes_cli import voice_ready_cue as cue
from tools import voice_mode


@pytest.fixture
def rig(monkeypatch):
    config = {'voice': {'ready_cue': {'enabled': True}}}
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: config)
    cli = SimpleNamespace(_voice_tts=True, _voice_mode=True,
                          _voice_beeps_enabled=lambda: False)
    beep = Mock()
    monkeypatch.setattr(voice_mode, 'play_beep', beep)
    return cli, config, beep


def test_ready_cue_independent_of_other_beeps(rig):
    cli, _, beep = rig
    cue.play_ready_cue(cli)
    beep.assert_called_once_with(frequency=660, duration=0.20, count=1)


def test_disabled_preserves_legacy_beep_preference(rig):
    cli, config, beep = rig
    config['voice']['ready_cue']['enabled'] = False
    cue.play_ready_cue(cli)
    beep.assert_not_called()
    cli._voice_beeps_enabled = lambda: True
    cue.play_ready_cue(cli)
    beep.assert_called_once_with(frequency=880, count=1)


def test_speaker_failure_does_not_break_capture_caller(rig):
    cli, _, beep = rig
    beep.side_effect = OSError('speaker unavailable')
    cue.play_ready_cue(cli)


@pytest.mark.parametrize('raw', [None, [], 'bad', 1])
def test_malformed_setting_is_disabled(rig, raw):
    _, config, _ = rig
    config['voice']['ready_cue'] = raw
    assert not cue.settings()['enabled']


def test_introduction_once_without_llm(rig, monkeypatch, tmp_path):
    cli, _, _ = rig
    cache = Mock(return_value=tmp_path/'intro.wav')
    play = Mock(return_value=True)
    monkeypatch.setattr('hermes_cli.voice_wake_ack.cached_audio', cache)
    monkeypatch.setattr(voice_mode, 'play_audio_file', play)
    cue.announce_once(cli)
    cue.announce_once(cli)
    cache.assert_called_once()
    play.assert_called_once()


def test_introduction_requires_tts(rig, monkeypatch):
    cli, _, _ = rig
    cli._voice_tts = False
    cache = Mock()
    monkeypatch.setattr('hermes_cli.voice_wake_ack.cached_audio', cache)
    cue.announce_once(cli)
    cache.assert_not_called()
