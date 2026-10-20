import importlib.util
import json
import logging
from pathlib import Path
import socket
import threading
import time
from unittest.mock import Mock

import pytest
import yaml
from tools.external_wake import ExternalEOSDetector

spec=importlib.util.spec_from_file_location("end_phrase_service",Path(__file__).resolve().parents[2]/"demo-runtime/wake/service.py")
service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
EOS=2


def make_server(config=None):
    s=service.Server(logging.getLogger("end-phrase-test"),["Hello Intel"],EOS,config)
    c=Mock();s.arm(c,"recording","current",100)
    return s,c


def events(c):return [json.loads(x.args[0]) for x in c.sendall.call_args_list]


@pytest.mark.parametrize("text",["thank you","THANK YOU!","Weather please, thank you.","thank you followed by background speech","Please explain thank you in French","thank   you", "Ｔｈａｎｋ ｙｏｕ"])
def test_partial_phrase_ends_while_vad_remains_active_once(text):
    s,c=make_server()
    s.result(101,0,text.encode())
    s.result(101,0,text.encode())
    s.result(101,EOS,b"")
    e=events(c)
    assert len(e)==1 and e[0]["reason"]=="end_phrase"
    assert e[0]["recording_id"]=="current" and e[0]["alias"]=="thank you"


@pytest.mark.parametrize("text",["thank","you","thank yourself","nothank you","hello"," "])
def test_requires_complete_phrase_on_word_boundary(text):
    s,c=make_server();s.result(101,0,text.encode());c.sendall.assert_not_called()


def test_disable_keyword_preserves_native_eos():
    s,c=make_server({"enabled":False,"phrase":"thank you"})
    s.result(101,0,b"thank you");c.sendall.assert_not_called()
    s.result(101,EOS,b"")
    assert events(c)[0]["reason"]=="aisp_eos"


def test_custom_phrase_replaces_default_without_cli_configuration():
    s,c=make_server({"enabled":True,"phrase":"all done"})
    s.result(101,0,b"thank you");c.sendall.assert_not_called()
    s.result(101,0,b"My request, ALL DONE.")
    assert events(c)[0]["alias"]=="all done"


def test_native_eos_first_prevents_later_keyword_duplicate():
    s,c=make_server();s.result(101,EOS,b"What time is it?");s.result(102,0,b"thank you")
    assert len(events(c))==1 and events(c)[0]["reason"]=="aisp_eos"


def test_keyword_does_not_trigger_in_wake_mode_or_without_subscriber():
    s,c=make_server();s.arm(c,"wake",None,100);s.result(101,0,b"thank you")
    c.sendall.assert_not_called();s.conn=None;s.result(102,0,b"thank you")
    c.sendall.assert_not_called()


def test_old_keyword_is_not_replayed_and_old_eos_stays_ignored():
    s,c=make_server();s.conn=None;s.result(99,0,b"Background thank you")
    s.arm(c,"recording","next",100)
    s.result(99,0,b"Background thank you")
    s.result(99,EOS,b"Background thank you")
    c.sendall.assert_not_called()


def test_new_keyword_suffix_on_continuous_old_segment_can_end_recording():
    s,c=make_server();s.conn=None;s.result(99,0,b"Background conversation")
    s.arm(c,"recording","next",100)
    s.result(99,EOS,b"Background conversation more speech")
    c.sendall.assert_not_called()
    s.result(99,0,b"Background conversation more speech thank you and background continues")
    assert events(c)[0]["reason"]=="end_phrase" and events(c)[0]["recording_id"]=="next"


@pytest.mark.parametrize("before,after",[(None,"thank you"),("Old thank","Old thank you"),("Old unrelated","Different revision thank you"),("Old no","Old nothank you")])
def test_unknown_revised_or_boundary_spanning_old_phrase_is_rejected(before,after):
    s,c=make_server();s.conn=None
    if before is not None:s.result(99,0,before.encode())
    s.arm(c,"recording","next",100);s.result(99,0,after.encode())
    c.sendall.assert_not_called()


@pytest.mark.parametrize("cfg",[None,False,"thank you",{}, {"enabled":1,"phrase":"thank you"},{"enabled":True,"phrase":""},{"enabled":True,"phrase":"!!!"},{"enabled":True,"phrase":"a\nb"},{"enabled":False,"phrase":None},{"enabled":True,"phrase":"done","extra":1}])
def test_bad_end_phrase_config_rejected(tmp_path,cfg):
    value=service.load_config();value["end_phrase"]=cfg
    p=tmp_path/"config.yaml";p.write_text(yaml.safe_dump(value))
    with pytest.raises(ValueError,match="end_phrase"):service.load_config(p)


def test_old_yaml_defaults_to_enabled_thank_you_and_keeps_asr_hints(tmp_path):
    cfg=service.load_config();cfg.pop("end_phrase")
    p=tmp_path/"config.yaml";p.write_text(yaml.safe_dump(cfg))
    loaded=service.load_config(p)
    assert loaded["end_phrase"]=={"enabled":True,"phrase":"thank you"}
    assert loaded["asr"]==cfg["asr"]


@pytest.fixture
def socket_service(tmp_path):
    s=service.Server(logging.getLogger("end-phrase-socket"),["Hello Intel"],EOS,{"enabled":True,"phrase":"all done"})
    alive=threading.Event();alive.set();native=Mock(capture_alive=alive.is_set)
    path=tmp_path/"service.sock";errors=[]
    def run():
        try:s.serve(native,path)
        except RuntimeError:
            if alive.is_set():errors.append(True)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    until=time.monotonic()+5
    while not path.exists() and time.monotonic()<until:time.sleep(.01)
    assert path.exists()
    yield s,path
    alive.clear();thread.join(4)
    assert not thread.is_alive() and not errors


def test_real_socket_keyword_then_native_eos_propagate_reason_and_identity(socket_service):
    s,path=socket_service;d=ExternalEOSDetector({"socket":str(path)});done=threading.Event();callback=Mock(side_effect=done.set)
    try:
        d.start(callback,16000,200)
        assert d.phrase=="all done"
        old=s.armed_after+1
        s.result(old,0,b"Please help, all done")
        assert done.wait(3) and d.last_reason=="end_phrase"
        s.result(old,EOS,b"");callback.assert_called_once()
        d.stop();done.clear();d.start(callback,16000,200)
        s.result(old,0,b"Please help, all done")
        assert callback.call_count==1
        s.result(s.armed_after+1,EOS,b"New question")
        assert done.wait(3) and d.last_reason=="aisp_eos" and callback.call_count==2
    finally:d.stop()


def test_client_rejects_wrong_recording_or_unknown_reason():
    d=ExternalEOSDetector({});d.recording_id="current"
    for message in [{"event":"end_recording","recording_id":"old","reason":"end_phrase"},{"event":"end_recording","recording_id":"current","reason":"unknown"}]:assert not d.matched(message)
    assert d.last_reason is None


def test_client_ready_setting_updates_display_phrase_and_retains_legacy_compatibility():
    d=ExternalEOSDetector({});d.recording_id="current"
    base={"event":"ready","mode":"recording","recording_id":"current","endpoint":"aisp-eos"}
    assert d.ready({**base,"end_phrase":{"enabled":True,"phrase":"finish now"}})
    assert d.phrase=="finish now"
    assert d.ready({**base,"end_phrase":{"enabled":False,"phrase":"finish now"}})
    assert d.phrase is None
    assert d.ready(base) and d.phrase is None
