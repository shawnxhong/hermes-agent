import json
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from tools import recording_quiet as quiet


def cli(): return SimpleNamespace(_voice_lock=threading.Lock(),_voice_recording=False,_voice_mode=True)


def test_recording_start_failure_releases_and_success_holds(monkeypatch):
    monkeypatch.setattr(quiet,'SOCKET',Mock(exists=lambda: True))
    request=Mock(return_value={'ok':True,'active':True})
    monkeypatch.setattr(quiet,'request',request)
    instance=cli()
    @quiet.recording_start
    def fail(c): raise RuntimeError('microphone')
    with pytest.raises(RuntimeError): fail(instance)
    assert [c.args[0] for c in request.call_args_list]==['acquire','release']
    @quiet.recording_start
    def start(c): c._voice_recording=True
    start(instance)
    lease=instance._recording_quiet
    assert not lease.done.is_set()
    # Wake ack and recording share one token, never save an already-lowered volume.
    assert quiet.begin(instance) is lease
    quiet.end(instance,lease)
    assert lease.done.is_set()


def test_old_cleanup_cannot_release_new_recording(monkeypatch):
    instance=cli();old=Mock();new=Mock();instance._recording_quiet=new
    quiet.end(instance,old)
    new.close.assert_not_called()
    assert instance._recording_quiet is new


def test_real_unix_request_and_server_error(tmp_path):
    path=tmp_path/'quiet.sock';server=socket.socket(socket.AF_UNIX)
    server.bind(str(path));server.listen()
    got=[]
    def serve():
        with server.accept()[0] as conn:
            got.append(json.loads(conn.makefile('rb').readline()))
            conn.sendall(b'{"ok":true,"active":true}\n')
    t=threading.Thread(target=serve);t.start()
    try:
        assert quiet.request('acquire','a'*32,path)['active']
        t.join(2);assert not t.is_alive()
        assert got==[{'action':'acquire','token':'a'*32}]
    finally: server.close()


def test_host_unavailable_never_blocks_recording(monkeypatch):
    monkeypatch.setattr(quiet,'SOCKET',Mock(exists=lambda: True))
    monkeypatch.setattr(quiet,'request',Mock(side_effect=OSError('offline')))
    instance=cli()
    @quiet.recording_start
    def start(c): c._voice_recording=True
    start(instance)
    assert instance._voice_recording
    assert instance._recording_quiet.done.is_set()


@pytest.mark.parametrize('value',[float('nan'),float('inf'),True,-1,121])
def test_bad_timeout_rejected(value):
    config={'enabled':True,'fan':{'enabled':True,'control_percent':30,'release_temperature_c':80},
            'output':{'enabled':True,'max_volume_percent':20},'max_duration_seconds':value}
    with pytest.raises(ValueError):quiet.validate_config(config)
