import importlib.util
import socketserver
import sys
import threading
from pathlib import Path

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
