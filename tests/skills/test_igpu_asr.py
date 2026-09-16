"""Local ASR transport, silence gating and native provider contracts."""
import importlib.util
import json
import socket
import socketserver
import struct
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/plugins/local-igpu-asr'


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    spec = importlib.util.spec_from_file_location('igpu_asr_test_plugin', ROOT/'__init__.py',
                                               submodule_search_locations=[str(ROOT)])
    plugin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, plugin)
    spec.loader.exec_module(plugin)
    spec = importlib.util.spec_from_file_location('igpu_asr_worker_test', ROOT/'worker.py')
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return plugin, worker


def test_silence_never_reaches_gpu(modules):
    _, worker = modules
    pipe, collect = Mock(), Mock()
    result = worker.transcribe_samples(np.zeros(16000), pipe, lambda *a, **k: [], collect)
    assert result['success'] and result['no_speech'] and result['transcript'] == ''
    pipe.generate.assert_not_called()
    collect.assert_not_called()


def test_chunks_english_and_greedy_without_cross_turn_context(modules):
    _, worker = modules
    pipe = Mock()
    pipe.generate.side_effect = [SimpleNamespace(texts=['Hello Intel.']), SimpleNamespace(texts=['Next.'])]
    collect = Mock(return_value=([np.zeros(10), np.zeros(20)], []))
    result = worker.transcribe_samples(np.zeros(100), pipe, Mock(return_value=[{'start':0,'end':100}]), collect)
    assert result['transcript'] == 'Hello Intel. Next.'
    for call in pipe.generate.call_args_list:
        assert call.kwargs['language'] == '<|en|>'
        assert call.kwargs['num_beams'] == 1


def test_duration_limit(modules):
    _, worker = modules
    with pytest.raises(ValueError):
        worker.transcribe_samples(np.zeros(16000*181), Mock(), Mock(), Mock())


def test_oversized_and_truncated_frames(modules):
    plugin, _ = modules
    transport = sys.modules[plugin.__package__+'.transport']
    for data in [struct.pack('!I', 1000), struct.pack('!I', 5)+b'ab']:
        a,b = socket.socketpair()
        with a,b:
            a.sendall(data); a.shutdown(socket.SHUT_WR)
            with pytest.raises((ValueError, ConnectionError)):
                transport.receive(b, 100)


@pytest.mark.parametrize('response', [
    {'success':True,'transcript':'Hello Intel.'},
    {'success':True,'transcript':'','no_speech':True},
    {'success':False,'transcript':'','error':'GPU failure'},
])
def test_real_socket_provider_roundtrip(modules, tmp_path, monkeypatch, response):
    plugin, worker = modules
    path = tmp_path/'asr.sock'
    audio = tmp_path/'input.wav'; audio.write_bytes(b'test audio')
    with socketserver.UnixStreamServer(str(path), worker.Handler) as server:
        server.engine = lambda payload: response
        thread = threading.Thread(target=server.handle_request, daemon=True); thread.start()
        monkeypatch.setattr(plugin, 'settings', lambda: {'socket':str(path),'timeout':2})
        provider = plugin.IGPUTranscription()
        assert provider.is_available()
        result = provider.transcribe(str(audio), language='en')
        thread.join(2)
        assert not thread.is_alive()
        assert result == {**response,'provider':'openvino_igpu'}


def test_worker_failure_returns_error(modules, tmp_path):
    plugin, worker = modules
    transport = sys.modules[plugin.__package__+'.transport']
    with socketserver.UnixStreamServer(str(tmp_path/'x.sock'), worker.Handler) as server:
        server.engine = Mock(side_effect=ValueError('invalid audio'))
        thread = threading.Thread(target=server.handle_request, daemon=True); thread.start()
        result = transport.request(tmp_path/'x.sock', b'bad audio', timeout=2)
        thread.join(2)
        assert result['success'] is False and result['transcript'] == ''


def test_missing_service_is_explicit_no_fallback(modules, tmp_path, monkeypatch):
    plugin, _ = modules
    monkeypatch.setattr(plugin, 'settings', lambda: {'socket':str(tmp_path/'missing')})
    provider = plugin.IGPUTranscription()
    assert not provider.is_available()
    audio = tmp_path/'audio.wav'; audio.write_bytes(b'audio')
    assert provider.transcribe(str(audio))['success'] is False
    assert provider.transcribe(str(audio), language='zh')['success'] is False


def test_native_registration(modules):
    plugin, _ = modules
    ctx = Mock()
    plugin.register(ctx)
    assert ctx.register_transcription_provider.call_args.args[0].name == 'openvino_igpu'
