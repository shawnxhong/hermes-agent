"""Host button lifecycle contracts; no real audio, network or user config."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli import HermesCLI
from hermes_cli import voice_startup_cue


@pytest.fixture
def box():
    path = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/hermes-box-voice.py'
    spec = importlib.util.spec_from_file_location('box_voice_tests', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_optional_startup_audio_pauses_and_plays_once(monkeypatch, tmp_path):
    from tools import voice_mode, wake_word
    from hermes_cli import config
    audio = tmp_path / 'standby.wav'
    audio.write_bytes(b'test')
    monkeypatch.setenv('HERMES_CLI_WAKE_READY_CUE', '1')
    monkeypatch.setattr(config, 'load_config', lambda: {'voice': {'startup_cue_file': str(audio)}})
    order = []
    monkeypatch.setattr(wake_word, 'pause_listening', lambda **kw: order.append('pause') or True)
    monkeypatch.setattr(wake_word, 'resume_listening', lambda **kw: order.append('resume') or True)
    monkeypatch.setattr(voice_mode, 'play_audio_file', lambda path: order.append('audio') or True)
    monkeypatch.setattr(voice_mode, 'play_beep', Mock())
    monkeypatch.setattr('cli.time.sleep', lambda _: None)
    cli = SimpleNamespace(_wake_suspended=False)
    assert HermesCLI._prepare_initial_wake_listener(cli)
    assert HermesCLI._prepare_initial_wake_listener(cli)
    assert order == ['pause', 'audio', 'resume']
    voice_mode.play_beep.assert_not_called()


def test_bad_startup_audio_cannot_claim_ready(monkeypatch, tmp_path):
    from tools import wake_word
    from hermes_cli import config
    monkeypatch.setenv('HERMES_CLI_WAKE_READY_CUE', '1')
    monkeypatch.setattr(config, 'load_config', lambda: {'voice': {'startup_cue_file': str(tmp_path/'missing.wav')}})
    monkeypatch.setattr(wake_word, 'pause_listening', lambda **kw: True)
    resume = Mock()
    monkeypatch.setattr(wake_word, 'resume_listening', resume)
    cli = SimpleNamespace(_wake_suspended=False)
    assert not HermesCLI._prepare_initial_wake_listener(cli)
    assert not getattr(cli, '_wake_ready_cue_played', False)
    resume.assert_not_called()


@pytest.mark.linux_only
def test_real_pty_start_and_explicit_stop_say_goodbye_once(box):
    events = []
    child = "import time; print('Wake word listening',flush=True); time.sleep(30)"
    controller = box.VoiceProcess([sys.executable, '-u', '-c', child], lambda: events.append('goodbye'))
    def notify(message):
        if message.startswith('READY=1'):
            events.append('ready')
            controller.request_stop()
            controller.request_stop()
    controller.send_notify = notify
    assert controller.run() == 0
    assert events == ['ready', 'goodbye']
    assert controller.exit_status is not None


@pytest.mark.linux_only
def test_real_pty_failure_never_says_goodbye(box):
    goodbye = Mock()
    controller = box.VoiceProcess([sys.executable, '-c', 'raise SystemExit(2)'], goodbye)
    with pytest.raises(RuntimeError, match='exited unexpectedly'):
        controller.run()
    goodbye.assert_not_called()


@pytest.mark.linux_only
def test_real_pty_readiness_timeout_never_says_goodbye(box):
    goodbye = Mock()
    controller = box.VoiceProcess([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 goodbye, ready_timeout=2)
    with pytest.raises(RuntimeError, match='did not become ready'):
        controller.run()
    goodbye.assert_not_called()


@pytest.mark.linux_only
@pytest.mark.parametrize(('action', 'state'), [('start', 'ready'), ('stop', 'off'), ('stop', 'failed')])
def test_idempotent_buttons_do_not_invoke_service(box, tmp_path, monkeypatch, action, state):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    monkeypatch.setattr(box, 'service_status', lambda: {'state': state, 'pid': 1})
    invoke = Mock()
    monkeypatch.setattr(box.subprocess, 'run', invoke)
    assert box.control(action)['changed'] is False
    invoke.assert_not_called()


@pytest.mark.linux_only
@pytest.mark.parametrize(('state', 'action'), [('off', 'start'), ('ready', 'stop')])
def test_toggle_targets_only_voice_service(box, tmp_path, monkeypatch, state, action):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    monkeypatch.setattr(box, 'service_status', lambda: {'state': state, 'pid': 1})
    invoke = Mock()
    monkeypatch.setattr(box.subprocess, 'run', invoke)
    box.control('toggle')
    invoke.assert_called_once_with(['systemctl', '--user', action, box.UNIT], check=True, timeout=125)
