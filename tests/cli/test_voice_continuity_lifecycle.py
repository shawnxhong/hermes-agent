import threading
from unittest.mock import Mock

import pytest

from hermes_cli.voice_continuity_store import ContinuityStore


@pytest.fixture
def voice_cli(monkeypatch):
    import cli as module
    from tools import voice_mode, wake_word
    monkeypatch.setattr(module,'CLI_CONFIG',{'agent':{},'model':{}})
    monkeypatch.setattr(module,'_sync_process_session_id',lambda value:None)
    monkeypatch.setattr('hermes_cli.voice_continuity.enabled',lambda:True)
    monkeypatch.setattr('hermes_cli.config.load_config',lambda:{'voice':{'auto_tts':True}})
    monkeypatch.setattr(wake_word,'pause_listening',lambda **kw:True)
    monkeypatch.setattr(wake_word,'get_last_match',lambda:None)
    monkeypatch.setattr(voice_mode,'stop_playback',lambda:None)
    monkeypatch.setattr(voice_mode,'detect_audio_environment',lambda:{'available':True})
    monkeypatch.setattr(voice_mode,'check_voice_requirements',lambda:{'available':True})
    capture=Mock();monkeypatch.setattr('hermes_cli.voice_wake_ack.start_wake_capture',capture)
    instance=module.HermesCLI.__new__(module.HermesCLI)
    instance.__dict__.update(session_id='original',agent=None,conversation_history=[],
        _session_db=None,_agent_running=False,_voice_recording=False,_voice_processing=False,
        _voice_mode=True,_voice_tts=True,_voice_continuous=False,_voice_recorder=None,
        _voice_lock=threading.Lock(),_voice_tts_done=threading.Event(),
        _voice_tts_stop=threading.Event(),_tts_lease_async=Mock(),
        _voice_record_key_label=lambda:'Ctrl+B',_wake_start_new_session=True)
    return instance,capture


def test_real_wake_handler_retains_idle_session_beyond_old_timeout(voice_cli,monkeypatch):
    instance,capture=voice_cli
    monkeypatch.setattr('hermes_cli.voice_followup.time.monotonic',lambda:1000)
    instance._voice_followup_resume=('original',1)
    instance._on_wake_word()
    assert instance.session_id=='original'
    capture.assert_called_once_with(instance)


@pytest.mark.parametrize('restart',['wake','enable'])
def test_explicit_voice_stop_rotates_session_on_next_activation(voice_cli,restart):
    instance,_=voice_cli
    instance._disable_voice_mode()
    assert not instance._voice_mode
    if restart=='wake':instance._on_wake_word()
    else:instance._enable_voice_mode()
    assert instance.session_id!='original' and instance._voice_mode
    assert not instance._voice_continuity_ended


def test_real_new_session_cannot_access_previous_topic(voice_cli):
    instance,_=voice_cli;store=ContinuityStore()
    old=store.start(instance.session_id,'A private old report')
    instance.new_session(silent=True)
    assert instance.session_id!='original'
    assert store.topics(instance.session_id)==[]
    assert store.current('original')['id']==old['id']
