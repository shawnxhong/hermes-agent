from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hermes_cli import voice_ready_cue as cue
from tools import voice_mode


@pytest.fixture
def rig(monkeypatch, tmp_path):
    config = {'voice': {'ready_cue': {'enabled': True}}}
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: config)
    cli = SimpleNamespace(_voice_tts=True, _voice_mode=True,
                          _voice_beeps_enabled=lambda: False)
    beep = Mock()
    monkeypatch.setattr(voice_mode, 'play_beep', beep)
    monkeypatch.setattr(cue.time, 'sleep', lambda _: None)
    monkeypatch.setattr('hermes_cli.voice_wake_ack.cached_audio', lambda cfg: tmp_path/'intro.wav')
    monkeypatch.setattr(voice_mode, 'play_audio_file', Mock(return_value=True))
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


def test_first_question_explains_then_gaps_then_cues_only_once(rig, monkeypatch):
    cli, _, beep = rig
    order = []
    def intro(path):
        order.append('intro')
        return True
    monkeypatch.setattr(voice_mode, 'play_audio_file', intro)
    monkeypatch.setattr(cue.time, 'sleep', lambda seconds: order.append('gap'))
    beep.side_effect = lambda **kw: order.append('tone')
    cue.play_ready_cue(cli)
    cue.play_ready_cue(cli)
    assert order == ['intro', 'gap', 'tone', 'gap', 'tone']


def test_gap_precedes_tone(rig, monkeypatch):
    cli, _, beep = rig
    order = []
    monkeypatch.setattr(cue.time, 'sleep', lambda seconds: order.append(seconds))
    beep.side_effect = lambda **kw: order.append('tone')
    cue.play_ready_cue(cli)
    assert order == [0.65, 'tone']


def test_failed_intro_can_retry_on_next_activation(rig, monkeypatch, tmp_path):
    cli, _, _ = rig
    monkeypatch.setattr('hermes_cli.voice_wake_ack.cached_audio', lambda cfg: tmp_path/'intro.wav')
    play = Mock(side_effect=[False, True])
    monkeypatch.setattr(voice_mode, 'play_audio_file', play)
    cue.announce_once(cli)
    assert not getattr(cli, '_voice_ready_intro_done', False)
    cue.announce_once(cli)
    assert cli._voice_ready_intro_done


def test_real_wake_start_path_does_not_announce(monkeypatch):
    from cli import HermesCLI
    from tools import wake_word
    cli = HermesCLI.__new__(HermesCLI)
    cli._start_wake_watchdog = Mock()
    monkeypatch.setattr(wake_word, 'load_wake_word_config', lambda: {})
    monkeypatch.setattr(wake_word, 'check_wake_word_requirements', lambda cfg: {'available': True})
    order = []
    monkeypatch.setattr(cue, 'announce_once', lambda obj, **kw: order.append(('intro', kw)))
    monkeypatch.setattr(wake_word, 'start_listening', lambda *a, **kw: order.append(('listen', {})))
    assert cli._start_wake_word_listener()
    assert order == [('listen', {})]
