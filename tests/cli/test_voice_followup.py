"""Same-session ordinary questions must not become runaway recording loops."""
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from hermes_cli import voice_followup as followup
from tools import voice_mode


@pytest.fixture
def rig(monkeypatch, tmp_path):
    cfg = dict(enabled=True, timeout_seconds=30, resume_seconds=120,
               playback_timeout_seconds=120)
    monkeypatch.setattr(followup, 'settings', lambda: cfg)
    cli = SimpleNamespace(session_id='trip-1', _voice_lock=threading.Lock(),
        _voice_mode=True, _voice_tts=True, _voice_continuous=False,
        _voice_recording=False, _voice_processing=False, _agent_running=False,
        _last_turn_interrupted=False, _pending_input=queue.Queue(),
        _voice_tts_done=threading.Event(), _voice_fd_active=threading.Event(),
        _voice_beeps_enabled=lambda: True, _voice_stt_model=lambda: None,
        _disable_voice_mode=Mock())
    cli._voice_tts_done.set()
    wav = tmp_path/'answer.wav'
    wav.write_bytes(b'audio-fixture')
    monkeypatch.setattr(voice_mode, 'is_audio_output_active', lambda: False)
    beep=Mock(); monkeypatch.setattr(voice_mode, 'play_beep', beep)
    listen=Mock(return_value=str(wav));monkeypatch.setattr(voice_mode,'full_duplex_listen',listen)
    transcribe=Mock(return_value={'success':True,'transcript':'From Seattle, three days in October.'})
    monkeypatch.setattr(voice_mode,'transcribe_recording',transcribe)
    monkeypatch.setattr(voice_mode,'is_voice_stop_phrase',lambda s:s=='stop')
    monkeypatch.setattr(voice_mode,'is_tts_echo',lambda a,b:a==b)
    # Execute the actual capture lifecycle synchronously for deterministic tests.
    class InlineThread:
        def __init__(self, target, args, **kwargs):self.target,self.args=target,args
        def start(self):self.target(*self.args)
    monkeypatch.setattr(followup.threading,'Thread',InlineThread)
    return cli,cfg,wav,listen,transcribe,beep


def start(cli, response='When are you going?', voice=True):
    from cli import _VoiceInputMessage
    return followup.start_followup(cli,response,voice_input=voice,make_message=_VoiceInputMessage)


def test_answer_is_voice_input_in_same_session(rig):
    cli,_,wav,listen,_,beep=rig
    assert start(cli)
    from cli import _VoiceInputMessage
    answer=cli._pending_input.get_nowait()
    assert isinstance(answer,_VoiceInputMessage)
    assert 'Seattle' in str(answer) and cli.session_id=='trip-1'
    assert not cli._voice_processing and not wav.exists()
    assert cli._voice_followup_resume is None
    beep.assert_called_once();listen.assert_called_once()


@pytest.mark.parametrize('response,voice',[('When?',False),('Done.',True),('',True)])
def test_no_window_for_typed_or_final_statement(rig,response,voice):
    cli,_,_,listen,_,_=rig
    assert not start(cli,response,voice)
    listen.assert_not_called()


@pytest.mark.parametrize('field',['_voice_recording','_voice_processing','_agent_running',
                                '_voice_continuous','_last_turn_interrupted'])
def test_busy_or_interrupted_does_not_arm(rig,field):
    cli,_,_,listen,_,_=rig;setattr(cli,field,True)
    assert not start(cli);listen.assert_not_called()


def test_timeout_retains_one_bounded_wake_resume(rig,monkeypatch):
    cli,cfg,_,listen,transcribe,_=rig
    clock=[10.0];monkeypatch.setattr(followup.time,'monotonic',lambda:clock[0])
    def silence(stop,**kwargs):
        assert not stop();clock[0]+=cfg['timeout_seconds']+1;assert stop()
    listen.side_effect=silence
    assert start(cli) and cli._pending_input.empty() and not cli._voice_processing
    transcribe.assert_not_called();assert followup.resume_question_session(cli)
    assert not followup.resume_question_session(cli)


@pytest.mark.parametrize('case',['expired','other_session'])
def test_stale_resume_never_reuses_another_session(rig,case):
    cli,*_=rig
    cli._voice_followup_resume=('other' if case=='other_session' else cli.session_id,
                                followup.time.monotonic()+(-1 if case=='expired' else 60))
    assert not followup.resume_question_session(cli)


@pytest.mark.parametrize('transcript',['stop','When are you going?',''])
def test_stop_echo_or_empty_never_submitted(rig,transcript):
    cli,_,_,_,transcribe,_=rig
    transcribe.return_value={'success':True,'transcript':transcript}
    assert start(cli) and cli._pending_input.empty() and not cli._voice_processing
    if transcript=='stop':cli._disable_voice_mode.assert_called_once()


def test_new_input_preempts_capture(rig):
    cli,_,_,listen,transcribe,_=rig
    def capture(stop,**kwargs):
        cli._pending_input.put('typed override');assert stop();return None
    listen.side_effect=capture
    assert start(cli);transcribe.assert_not_called()
    assert cli._pending_input.get_nowait()=='typed override'


def test_transcription_racing_new_session_is_discarded(rig):
    cli,_,_,_,transcribe,_=rig
    def transcribe_new(*args,**kwargs):
        cli.session_id='different';return {'success':True,'transcript':'Seattle'}
    transcribe.side_effect=transcribe_new
    assert start(cli) and cli._pending_input.empty()


def test_playback_must_finish_before_microphone(rig,monkeypatch):
    cli,_,_,listen,_,_=rig
    class PendingPlayback:
        def wait(self,timeout):followup.cancel_followup(cli);return False
    cli._voice_tts_done=PendingPlayback()
    assert start(cli);listen.assert_not_called();assert not cli._voice_processing


def test_policy_uses_normal_question_only_when_enabled():
    from hermes_cli.voice_response_policy import build_voice_turn_prefix
    assert 'use the clarify tool' in build_voice_turn_prefix()
    enabled=build_voice_turn_prefix(followup_enabled=True)
    assert 'use the clarify tool' not in enabled and 'ordinary final reply' in enabled


@pytest.mark.parametrize('raw,expected', [(None,30), ('bad',30), (float('nan'),30),
                                        (-10,1), (1000,60)])
def test_settings_bound_recording_time(monkeypatch,raw,expected):
    from hermes_cli import config
    monkeypatch.setattr(config,'load_config',lambda:{'voice':{'followup':{
        'enabled':True,'timeout_seconds':raw}}})
    assert followup.settings()['timeout_seconds']==expected


def test_cancel_clears_resume_and_signals_worker(rig):
    cli,*_=rig
    cli._voice_followup_resume=(cli.session_id,999999)
    cli._voice_followup_cancel=threading.Event()
    followup.cancel_followup(cli)
    assert cli._voice_followup_resume is None and cli._voice_followup_cancel.is_set()
