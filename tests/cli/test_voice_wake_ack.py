import json
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import pytest
from hermes_cli import voice_wake_ack as ack


@pytest.fixture
def rig(monkeypatch,tmp_path):
    from tools import voice_mode
    cli=SimpleNamespace(session_id='session',_voice_lock=threading.Lock(),
        _voice_recording=False,_voice_processing=False,_agent_running=False,
        _voice_mode=True,_voice_tts=True,_wake_word_active=True,
        _pending_input=queue.Queue(),_voice_tts_done=threading.Event(),
        _voice_start_recording=Mock())
    cfg={'enabled':True,'text':"Hi, I'm here.",'tts':{'provider':'local'}}
    monkeypatch.setattr(ack,'settings',lambda:cfg)
    cache=Mock(return_value=tmp_path/'ack.wav');monkeypatch.setattr(ack,'cached_audio',cache)
    play=Mock(return_value=True);monkeypatch.setattr(voice_mode,'play_audio_file',play)
    monkeypatch.setattr(ack.time,'sleep',lambda _:None)
    return cli,cfg,cache,play


def test_ack_finishes_before_capture_and_watchdog_stays_busy(rig):
    cli,_,cache,play=rig
    events=[]
    def playing(path):
        assert cli._voice_processing and not cli._voice_tts_done.is_set()
        cli._voice_start_recording.assert_not_called()
        events.append('play');return True
    play.side_effect=playing
    def record():
        assert cli._voice_tts_done.is_set() and cli._voice_processing
        events.append('capture')
    cli._voice_start_recording.side_effect=record
    ack.start_wake_capture(cli)
    assert events==['play','capture'] and not cli._voice_processing
    assert cli._voice_last_tts_text=="Hi, I'm here."


@pytest.mark.parametrize('field',['_voice_recording','_voice_processing','_agent_running'])
def test_busy_wake_never_speaks_or_records(rig,field):
    cli,_,cache,play=rig;setattr(cli,field,True)
    ack.start_wake_capture(cli)
    cache.assert_not_called();play.assert_not_called();cli._voice_start_recording.assert_not_called()


@pytest.mark.parametrize('stage',['synthesis','playback'])
@pytest.mark.parametrize('cancel',['exit','stop','session','typed','wake_off'])
def test_cancelled_wake_does_not_start_recording(rig,stage,cancel):
    cli,_,cache,play=rig
    def interrupt(*args):
        if cancel=='exit':cli._should_exit=True
        elif cancel=='stop':cli._voice_mode=False
        elif cancel=='session':cli.session_id='new'
        elif cancel=='typed':cli._pending_input.put('typed task')
        else:cli._wake_word_active=False
        return cache.return_value if stage=='synthesis' else True
    (cache if stage=='synthesis' else play).side_effect=interrupt
    ack.start_wake_capture(cli)
    cli._voice_start_recording.assert_not_called()
    if stage=='synthesis':play.assert_not_called()
    assert not cli._voice_processing and cli._voice_tts_done.is_set()


@pytest.mark.parametrize('stage',['synthesis','playback'])
def test_failure_keeps_existing_capture_and_releases_busy_flag(rig,stage):
    cli,_,cache,play=rig
    (cache if stage=='synthesis' else play).side_effect=RuntimeError('audio unavailable')
    ack.start_wake_capture(cli)
    cli._voice_start_recording.assert_called_once()
    assert not cli._voice_processing and cli._voice_tts_done.is_set()


@pytest.mark.parametrize('disabled',['setting','tts'])
def test_disabled_preserves_capture_without_ack(rig,disabled):
    cli,cfg,cache,play=rig
    if disabled=='setting':cfg['enabled']=False
    else:cli._voice_tts=False
    ack.start_wake_capture(cli)
    cache.assert_not_called();play.assert_not_called();cli._voice_start_recording.assert_called_once()


def test_cache_uses_real_tts_entrypoint_and_invalidates_for_text_or_voice(monkeypatch):
    from tools import tts_tool
    calls=[]
    def synthesize(text,output_path):
        calls.append(text)
        with wave.open(output_path,'wb') as out:
            out.setparams((1,2,24000,0,'NONE','not compressed'))
            out.writeframes(b'\0\0'*2400)
        return json.dumps({'success':True,'file_path':output_path})
    monkeypatch.setattr(tts_tool,'text_to_speech_tool',synthesize)
    cfg={'text':"Hi, I'm here.",'tts':{'provider':'local','voice':'english'}}
    first=ack.cached_audio(cfg)
    assert first.is_file() and ack.cached_audio(cfg)==first and len(calls)==1
    cfg['text']='我在。'
    second=ack.cached_audio(cfg)
    assert second!=first and len(calls)==2
    cfg['tts']['voice']='chinese'
    assert ack.cached_audio(cfg)!=second and len(calls)==3


@pytest.mark.parametrize('raw',[None,True,'bad',{'text':None},{'text':''}])
def test_settings_are_shape_safe_and_opt_in(monkeypatch,raw):
    from hermes_cli import config
    monkeypatch.setattr(config,'load_config',lambda:{'voice':{'wake_ack':raw}})
    cfg=ack.settings()
    assert not cfg['enabled'] and cfg['text']=="Hi, I'm here."


def test_real_cli_wake_callback_pauses_then_acknowledges_then_captures(monkeypatch,rig):
    from cli import HermesCLI
    from tools import wake_word
    cli,_,cache,play=rig
    cli._wake_start_new_session=True;cli.new_session=Mock()
    cli._voice_followup_resume=(cli.session_id,ack.time.monotonic()+120)
    events=[]
    monkeypatch.setattr(wake_word,'pause_listening',lambda **kw:events.append('pause') or True)
    monkeypatch.setattr(wake_word,'get_last_match',lambda:None)
    play.side_effect=lambda path:events.append('ack') or True
    cli._voice_start_recording.side_effect=lambda:events.append('capture')
    HermesCLI._on_wake_word(cli)
    assert events==['pause','ack','capture']
    cli.new_session.assert_not_called()
