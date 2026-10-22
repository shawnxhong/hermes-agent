"""Forced ASR segment boundaries, including real socket control and races."""
import importlib.util
import json
import logging
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location('split_service', Path(__file__).resolve().parents[2] / 'demo-runtime/wake/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def server():
    return service.Server(logging.getLogger('split-test'), ['Hi Intel'], 2)


def request(s, reason='scene_ready', key='one'):
    return dict(instance=s.instance, reason=reason, request_id=key)


def finish(s, action, req=None):
    n=Mock();n.flush.side_effect=action
    task=s.force_segment(n,req or request(s));s.wait_segment(task)
    return task,n


def arm_split(s,task):
    c=Mock();s.arm(c,'wake',None,100000)
    s.segment_floor=task['floor'];task['awaiting_arm']=False
    for ts,flag,text in task['fresh']:
        s.result(ts,flag,text.encode())
    return c


def test_new_partial_before_flush_returns_is_not_old_audio():
    s=server();s.result(100,0,b'old')
    def flush():
        s.result(100,2,b'old Hi Intel')
        s.result(150,1,b'Hi Intel')
    task,_=finish(s,flush)
    c=arm_split(s,task)
    assert json.loads(c.sendall.call_args.args[0])['timestamp']==150
    s.result(150,0,b'Hi Intel');assert c.sendall.call_count==1


def test_late_callback_after_flush_before_arm_is_preserved():
    s=server();task,_=finish(s,lambda:None)
    s.result(150,1,b'Hi Intel')
    c=arm_split(s,task);assert c.sendall.call_count==1


def test_old_eos_and_old_delayed_callbacks_cannot_wake():
    s=server();s.result(100,0,b'old')
    task,_=finish(s,lambda:s.result(100,2,b'Hi Intel'))
    c=arm_split(s,task);c.sendall.assert_not_called()
    s.result(100,0,b'Hi Intel');c.sendall.assert_not_called()
    s.result(101,0,b'Hi Intel');assert c.sendall.call_count==1


def test_empty_flush_and_repeated_id_execute_once():
    s=server();task,n=finish(s,lambda:None)
    assert s.force_segment(n,request(s)) is task
    assert n.flush.call_count==1
    assert task['floor']==-1


def test_flush_without_expected_eos_fails_closed():
    s=server();s.result(100,0,b'old')
    with pytest.raises(RuntimeError,match='did not close'):
        finish(s,lambda:None)


def test_ended_new_segment_during_flush_is_not_replayed():
    s=server()
    def flush():
        s.result(100,2,b'old');s.result(110,1,b'Hi Intel');s.result(110,2,b'Hi Intel')
    task,_=finish(s,flush);c=arm_split(s,task);c.sendall.assert_not_called()
    s.result(120,1,b'Hi Intel');assert c.sendall.call_count==1


def test_pending_flush_does_not_deliver_or_start_second_flush():
    s=server();c=Mock();s.arm(c,'recording','rec',0)
    started=threading.Event();release=threading.Event()
    def flush():
        started.set();release.wait(2);s.result(100,2,b'thank you')
    n=Mock();n.flush.side_effect=flush
    task=s.force_segment(n,request(s));assert started.wait(1)
    try:
        with pytest.raises(RuntimeError,match='pending'):
            s.force_segment(n,request(s,key='two'))
        s.result(100,0,b'thank you');c.sendall.assert_not_called()
    finally:release.set()
    s.wait_segment(task);c.sendall.assert_not_called()


@pytest.mark.parametrize('enabled,identity,delivered',[(False,'rec',False),(True,'old',False),(True,'rec',True)])
def test_recording_request_cannot_affect_disabled_stale_or_finished_recording(enabled,identity,delivered):
    s=server();s.end_phrase['enabled']=enabled;s.arm(Mock(),'recording','rec',0);s.delivered=delivered
    with pytest.raises(ValueError):
        s.recording_segment(Mock(),{**request(s,'end_phrase'),'recording_id':identity})


def test_recording_force_eos_never_finishes_twice():
    s=server();c=Mock();s.arm(c,'recording','rec',0);s.result(100,0,b'question')
    n=Mock();n.flush.side_effect=lambda:s.result(100,2,b'thank you')
    task=s.recording_segment(n,{**request(s,'end_phrase'),'recording_id':'rec'})
    s.wait_segment(task);c.sendall.assert_not_called();assert s.delivered
    assert not task.get('awaiting_arm')


def test_recording_client_sends_control_before_callback_closes_socket():
    from tools.external_wake import ExternalEOSDetector
    import socket
    detector=ExternalEOSDetector({});left,right=socket.socketpair()
    detector.sock=left;detector.force_segment_version=1;detector.service_instance='instance'
    detector.recording_id='rec';scope=threading.Event();detector._audio_scope=scope
    detector._audio_callback=Mock(side_effect=detector.close)
    detector._finish(scope,'end_phrase')
    msg=json.loads(right.recv(4096));right.close()
    assert msg['recording_id']=='rec' and msg['reason']=='end_phrase'
    detector._finish(scope,'end_phrase');assert detector._audio_callback.call_count==1

@pytest.fixture
def socket_service(tmp_path):
    import time
    from types import SimpleNamespace
    s=server();running=threading.Event();running.set()
    native=SimpleNamespace(capture_alive=running.is_set,flush=Mock())
    path=tmp_path/'service.sock'
    def run():
        try:s.serve(native,path)
        except RuntimeError:
            assert not running.is_set()
    worker=threading.Thread(target=run,daemon=True);worker.start()
    deadline=time.monotonic()+3
    while not path.with_name('endpoint-config.json').exists():
        assert time.monotonic()<deadline
        time.sleep(.01)
    yield s,native,path
    running.clear();worker.join(4);assert not worker.is_alive()


def test_scene_socket_handshake_flushes_and_delivers_fresh_segment(socket_service):
    from tools.external_wake import ExternalWakeEngine
    import time
    s,n,path=socket_service;s.result(100,0,b'old')
    def flush():
        s.result(100,2,b'old Hi Intel');s.result(110,1,b'Hi Intel')
    n.flush.side_effect=flush
    engine=ExternalWakeEngine({'external':{'socket':str(path)}})
    engine.set_replay({'force_segment':'scene-one'})
    try:
        engine.reset();assert engine.force_segment_version==1
        deadline=time.monotonic()+2
        while not engine.process(None):
            assert time.monotonic()<deadline
            time.sleep(.01)
        assert engine.last_match[0]=='Hi Intel'
        engine.set_replay({'force_segment':'scene-one'});engine.reset()
        assert n.flush.call_count==1
    finally:engine.close()


def test_recording_socket_control_runs_after_local_completion(socket_service):
    from tools.external_wake import ExternalEOSDetector
    s,n,path=socket_service
    d=ExternalEOSDetector({'socket':str(path)})
    # Bypass keyword model initialization, retain the real subscription protocol.
    d._prepared={'instance':s.instance,'end_phrase':s.end_phrase};d._start_subscription(lambda:None)
    scope=threading.Event();d._audio_scope=scope;d._audio_callback=Mock(side_effect=d.close)
    s.result(int(service.time.monotonic()*1000)+1,0,b'question')
    old=max(s.open_segments)
    n.flush.side_effect=lambda:s.result(old,2,b'thank you')
    try:
        d._finish(scope,'end_phrase')
        import time
        deadline=time.monotonic()+2
        while s.split_task is None:
            assert time.monotonic()<deadline
            time.sleep(.01)
        s.wait_segment(s.split_task)
        assert n.flush.call_count==1 and d._audio_callback.call_count==1
    finally:d.stop()


def test_timeout_does_not_cancel_or_duplicate_flush():
    s=server();task={'done':Mock(),'request':{'request_id':'pending'},'error':None}
    task['done'].wait.return_value=False
    with pytest.raises(RuntimeError,match='pending'):s.wait_segment(task)
    task['done'].set.assert_not_called()
