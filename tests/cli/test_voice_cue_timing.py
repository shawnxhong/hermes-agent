from types import SimpleNamespace as NS
from unittest.mock import Mock
import queue
import threading
import wave

import numpy as np
import pytest
from hermes_cli import voice_wake_ack as ack
from tools import voice_mode as audio, recording_quiet as quiet


@pytest.fixture
def recorder(monkeypatch):
    stream=NS(time=10.0,start=Mock(),stop=Mock(),close=Mock())
    callbacks=[]
    def make_stream(**kwargs):
        callbacks.append(kwargs["callback"])
        return stream
    monkeypatch.setattr(audio,"_import_audio",lambda:(NS(InputStream=make_stream),np))
    monkeypatch.setattr(audio,"_default_input_samplerate",lambda sd:1000)
    r=audio.AudioRecorder()
    detector=NS(service_capture=True,phrase=None,start=Mock(),stop=Mock(),feed=Mock(),close=Mock())
    r._end_phrase_detector=detector
    monkeypatch.setattr(r,"prepare_endpoint",lambda:True)
    r.prepare_capture()
    return r,stream,callbacks[0],detector


def feed(cb, values, adc):
    data=np.asarray(values,dtype=np.int16).reshape(-1,1)
    cb(data,len(data),NS(inputBufferAdcTime=adc),None)


def test_preparation_keeps_idle_audio_out_and_reuses_stream(recorder):
    r,stream,cb,detector=recorder
    feed(cb,[3000]*100,9.9)
    r.prepare_capture()
    assert not r.is_recording and r._frames==[]
    detector.start.assert_not_called();detector.feed.assert_not_called()
    stream.start.assert_called_once()
    r.start(capture_after=10.0)
    stream.start.assert_called_once()
    r.cancel()


@pytest.mark.parametrize("adc,expected",[(9.9,[]),(9.995,[5,6,7,8,9]),(10.0,list(range(10))),(10.001,list(range(10)))])
def test_first_overlapping_block_is_trimmed_without_dropping_prefix(recorder,adc,expected):
    r,stream,cb,detector=recorder
    r.start(capture_after=10.0)
    feed(cb,range(10),adc)
    got=np.concatenate(r._frames).ravel().tolist() if r._frames else []
    assert got==expected
    if expected:assert detector.feed.call_args.args[0].ravel().tolist()==expected
    else:detector.feed.assert_not_called()
    r.cancel()


@pytest.mark.parametrize("adc",[None,True,float("nan"),float("inf")])
def test_invalid_adc_clock_never_admits_cue_audio(recorder,adc):
    r,_,cb,detector=recorder;r.start(capture_after=10.0)
    feed(cb,[3000]*100,adc)
    assert r._frames==[]
    r.cancel()


def cli_for(r):
    return NS(session_id="test",_scene_generation=0,_voice_lock=threading.Lock(),
        _voice_recording=False,_voice_processing=False,_agent_running=False,
        _voice_mode=True,_voice_tts=True,_wake_word_active=True,_should_exit=False,
        _pending_input=queue.Queue(),_voice_tts_done=threading.Event(),_voice_recorder=r,
        _pause_wake_word_for_manual_capture=lambda:True,_voice_beeps_enabled=lambda:True,
        _voice_record_key_label=lambda:"key",_app=None)


def test_real_cli_collects_first_word_during_endpoint_and_quiet_requests(recorder,monkeypatch,tmp_path):
    from cli import HermesCLI
    r,stream,cb,detector=recorder;cli=cli_for(r);events=[]
    monkeypatch.setattr(audio,"check_voice_requirements",lambda:{"audio_available":True,"stt_available":True})
    monkeypatch.setattr("tools.voice_endpoint.prepare_recording_endpoint",lambda r:None)
    monkeypatch.setattr("hermes_cli.config.load_config",lambda:{"voice":{}})
    def beep(**kwargs):
        assert kwargs["require_complete"] is True
        assert not r.is_recording
        events.append("beep")
        feed(cb,[32000]*120,10.0)
        stream.time=10.12
    monkeypatch.setattr(audio,"play_beep",beep)
    def endpoint(*args):
        events.append("endpoint")
        assert r.is_recording
        feed(cb,[32000]+[1000]*399,10.119)
    detector.start.side_effect=endpoint
    def acquire(instance):
        assert r.is_recording
        events.append("quiet")
        feed(cb,[2000]*400,10.519)
    monkeypatch.setattr(quiet,"begin",acquire)
    monkeypatch.setattr(audio,"_TEMP_DIR",str(tmp_path))
    try:
        HermesCLI._voice_start_recording(cli)
        assert events==["beep","endpoint","quiet"]
        samples=np.concatenate(r._frames).ravel()
        assert samples.tolist()==[1000]*399+[2000]*400
        path=r.stop()
        with wave.open(path) as f:
            assert f.getnframes()==799
            assert np.frombuffer(f.readframes(799),dtype=np.int16).tolist()==samples.tolist()
    finally:
        cli._voice_recording=False
        r.cancel()


def test_strict_beep_failure_does_not_record_or_acquire_quiet(recorder,monkeypatch):
    from cli import HermesCLI
    r,_,_,detector=recorder;cli=cli_for(r)
    monkeypatch.setattr(audio,"check_voice_requirements",lambda:{"audio_available":True,"stt_available":True})
    monkeypatch.setattr("tools.voice_endpoint.prepare_recording_endpoint",lambda r:None)
    monkeypatch.setattr("hermes_cli.config.load_config",lambda:{"voice":{}})
    monkeypatch.setattr(audio,"play_beep",Mock(side_effect=RuntimeError("cue failure")))
    acquire=Mock();monkeypatch.setattr(quiet,"begin",acquire)
    with pytest.raises(RuntimeError,match="cue failure"):HermesCLI._voice_start_recording(cli)
    assert not cli._voice_recording and not r.is_recording
    acquire.assert_not_called();detector.start.assert_not_called()


def test_ack_prepares_input_before_speaking_and_keeps_only_50ms_gap(recorder,monkeypatch,tmp_path):
    r,stream,_,_=recorder;cli=cli_for(r);events=[]
    monkeypatch.setattr(ack,"settings",lambda:{"enabled":True,"text":"Hi, Im here.","tts":{}})
    monkeypatch.setattr("tools.voice_endpoint.pending_hint_text",lambda:None)
    monkeypatch.setattr(ack,"cached_audio",lambda cfg:tmp_path/"ack.wav")
    prepare=r.prepare_capture
    monkeypatch.setattr(r,"prepare_capture",lambda:events.append("prepare") or prepare())
    monkeypatch.setattr(audio,"play_audio_file",lambda path:events.append("ack") or True)
    monkeypatch.setattr(ack,"time",NS(sleep=lambda gap:events.append(gap)))
    cli._voice_start_recording=lambda:events.append("record")
    ack.start_wake_capture(cli)
    assert events==["prepare","ack",0.05,"record"]
    assert not r.is_recording


def test_deferred_quiet_cleanup_releases_failed_recording(monkeypatch):
    cli=cli_for(None);lease=NS(done=threading.Event(),close=Mock())
    lease.close.side_effect=lease.done.set
    begin=Mock();monkeypatch.setattr(quiet,"begin",begin)
    @quiet.recording_start(after_cue=True)
    def recording(instance):
        assert not begin.called
        instance._recording_quiet=lease
        raise RuntimeError("failed after cue")
    with pytest.raises(RuntimeError):recording(cli)
    lease.close.assert_called_once()
    assert cli._recording_quiet is None


@pytest.mark.parametrize('failure',[False,True])
def test_prepare_confirmation_precedes_cue_and_failure_never_beeps(recorder,monkeypatch,failure):
    from cli import HermesCLI
    r,stream,cb,detector=recorder;cli=cli_for(r);events=[]
    monkeypatch.setattr(audio,'check_voice_requirements',lambda:{'audio_available':True,'stt_available':True})
    monkeypatch.setattr('tools.voice_endpoint.prepare_recording_endpoint',lambda r:None)
    monkeypatch.setattr('hermes_cli.config.load_config',lambda:{'voice':{}})
    def wait():
        assert not r.is_recording
        events.append('prepare_done')
        if failure:raise RuntimeError('flush pending')
    detector.wait_recording_prepare=wait
    detector.discard_recording_prepare=Mock()
    monkeypatch.setattr(audio,'play_beep',lambda **kwargs:events.append('beep'))
    monkeypatch.setattr(quiet,'begin',lambda cli:None)
    if failure:
        with pytest.raises(RuntimeError,match='flush pending'):
            HermesCLI._voice_start_recording(cli)
        assert events==['prepare_done'] and not cli._voice_recording
        detector.start.assert_not_called()
    else:
        HermesCLI._voice_start_recording(cli)
        assert events==['prepare_done','beep']
        detector.start.assert_called_once()
        cli._voice_recording=False;r.cancel()
