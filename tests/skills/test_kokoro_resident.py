import importlib.util
import socketserver
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]/'scripts/local-ovms'


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    import kokoro_client, kokoro_server
    monkeypatch.setattr(kokoro_client.signal,'signal',lambda *a:None)
    return kokoro_client,kokoro_server


def test_socket_roundtrip_wav_only(modules,tmp_path):
    client,server_module=modules
    path=tmp_path/'tts.sock'
    wav=b'RIFF'+bytes(4)+b'WAVE'+b'audio'
    with socketserver.UnixStreamServer(str(path),server_module.Handler) as server:
        received=[]
        def engine(value):
            received.append(server_module.validate(value));return wav
        server.engine=engine
        worker=threading.Thread(target=server.handle_request,daemon=True);worker.start()
        assert client.synthesize(path,'Hello.',timeout=2)==wav
        worker.join(2)
        assert received==[('Hello.',1.,'af_maple')]


@pytest.mark.parametrize('value',[{}, {'text':''},{'text':'x'*2001},
    {'text':'Hi','speed':True},{'text':'Hi','speed':99},{'text':'Hi','voice':'other'}])
def test_invalid_request_rejected(modules,value):
    with pytest.raises(ValueError):modules[1].validate(value)


def test_missing_service_falls_back_once(modules,tmp_path,monkeypatch):
    client,_=modules
    source=tmp_path/'input';source.write_text('Hello.')
    calls=[]
    monkeypatch.setattr(client.subprocess,'run',lambda *a,**k:calls.append((a,k)))
    monkeypatch.setattr(sys,'argv',['client','--socket',str(tmp_path/'missing'),
        '--input',str(source),'--output',str(tmp_path/'out.wav'),
        '--fallback-python',sys.executable,'--model-dir',str(tmp_path)])
    client.main()
    assert len(calls)==1
    assert calls[0][1]['timeout']==45


def test_cancelled_client_never_falls_back(modules,tmp_path,monkeypatch):
    client,_=modules
    source=tmp_path/'input';source.write_text('Hello.')
    calls=[]
    monkeypatch.setattr(client,'synthesize',lambda *a,**k:(_ for _ in ()).throw(SystemExit(143)))
    monkeypatch.setattr(client.subprocess,'run',lambda *a,**k:calls.append(a))
    monkeypatch.setattr(sys,'argv',['client','--socket','unused','--input',str(source),
        '--output',str(tmp_path/'out.wav'),'--fallback-python',sys.executable,'--model-dir',str(tmp_path)])
    with pytest.raises(SystemExit):client.main()
    assert calls==[]


def test_igpu_engine_uses_strict_openvino_session(modules,tmp_path,monkeypatch):
    _,server_module=modules
    session=SimpleNamespace(device_name='GPU.0')
    session_factory=Mock(return_value=session)
    kokoro=SimpleNamespace(from_session=Mock(return_value=Mock()))
    monkeypatch.setitem(sys.modules,'onnxruntime',SimpleNamespace())
    monkeypatch.setitem(sys.modules,'kokoro_onnx',SimpleNamespace(Kokoro=kokoro))
    monkeypatch.setitem(sys.modules,'kokoro_openvino',SimpleNamespace(
        OpenVINOSession=session_factory))
    monkeypatch.setattr(server_module.Engine,'__call__',lambda self,value:None)
    cache=tmp_path/'cache'
    engine=server_module.Engine(tmp_path,8,'igpu',cache)
    session_factory.assert_called_once_with(
        tmp_path/'kokoro-v1.1-zh-openvino.onnx',cache)
    kokoro.from_session.assert_called_once_with(
        session,str(tmp_path/'voices-v1.1-zh.bin'),
        vocab_config=str(tmp_path/'config.json'))
    assert engine.device=='GPU.0'


def test_engine_rejects_non_finite_audio(modules,monkeypatch):
    _,server_module=modules
    monkeypatch.setitem(sys.modules,'soundfile',SimpleNamespace())
    engine=server_module.Engine.__new__(server_module.Engine)
    engine.model=SimpleNamespace(create=lambda *args,**kwargs:(
        np.array([0.,np.nan],dtype=np.float32),24000))
    with pytest.raises(RuntimeError,match='non-finite'):
        engine({'text':'Hello.'})


@pytest.mark.parametrize('close_output', [False, True])
def test_command_cancel_stops_running_child(tmp_path, close_output):
    import shlex
    import time
    from tools import tts_tool
    cancel=threading.Event();started=tmp_path/'started';completed=tmp_path/'completed'
    code=('from pathlib import Path; import time, os; '
          + ('os.close(1); os.close(2); ' if close_output else '') +
          f'Path({str(started)!r}).touch(); time.sleep(5); Path({str(completed)!r}).touch()')
    command=shlex.join([sys.executable,'-c',code]);errors=[]
    def run():
        tts_tool._command_tts_cancel.event=cancel
        try:tts_tool._run_command_tts(command,10)
        except RuntimeError as error:errors.append(str(error))
        finally:tts_tool._command_tts_cancel.event=None
    worker=threading.Thread(target=run,daemon=True);worker.start()
    deadline=time.monotonic()+3
    while not started.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert started.exists()
    cancel.set();worker.join(3)
    assert not worker.is_alive() and errors==['TTS request cancelled']
    assert not completed.exists()
