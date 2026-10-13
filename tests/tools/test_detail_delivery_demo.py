import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT/'demo-runtime/plugins/services/email-queue'
sys.path.insert(0, str(PLUGIN))
import detail_delivery as d


def request(turn='turn-1', body='Useful details'):
    return {'scope': ['profile', 'session', turn], 'subject': 'A title', 'body': body}


def test_concurrent_dedup_and_frozen_target(tmp_path):
    store = d.DetailStore(tmp_path/'queue')
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: store.enqueue(request(), ['feishu:oc_one']), range(12)))
    assert sum(r['status'] == 'queued' for r in results) == 1
    assert store.enqueue(request(), ['recipient@example.com'])['channel'] == 'feishu'
    seen = []
    store.drain_one(lambda job, target: (seen.append(target['address']) or ('sent', None, False)))
    assert seen == ['feishu:oc_one']
    assert not store.drain_one(lambda *a: pytest.fail('duplicate execution'))
    assert store.enqueue(request('new-turn'), ['recipient@example.com'])['status'] == 'queued'


@pytest.mark.parametrize('address', ['wrong', 'feishu:bad', 'feishu:oc_a\n', ''])
def test_bad_targets_rejected(tmp_path, address):
    with pytest.raises(ValueError): d.DetailStore(tmp_path).enqueue(request(), [address])


def test_surrogate_rejected_without_job(tmp_path):
    store = d.DetailStore(tmp_path)
    with pytest.raises(ValueError): store.enqueue(request(body='\ud800'), ['feishu:oc_a'])
    assert store.status() == []


def test_config_hot_read_and_invalid_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('FEISHU_APP_ID', 'test-id')
    monkeypatch.setenv('FEISHU_APP_SECRET', 'test-secret')
    config = tmp_path/'config.yaml'
    config.write_text('detail_delivery: {channel: feishu, feishu: {target_id: oc_one}}')
    assert d.settings(tmp_path) == ['feishu:oc_one']
    config.write_text('detail_delivery: {channel: feishu, feishu: {target_id: oc_two}}')
    assert d.settings(tmp_path) == ['feishu:oc_two']
    monkeypatch.delenv('FEISHU_APP_SECRET')
    with pytest.raises(ValueError): d.settings(tmp_path)
    config.write_text('detail_delivery: {channel: unsupported}')
    with pytest.raises(ValueError): d.settings(tmp_path)


def test_chunk_byte_bound_and_lossless():
    text = 'Hello🌏\n' * 1500
    pieces = d.chunks(text)
    assert ''.join(pieces) == text
    assert all(len(p.encode()) <= 4000 for p in pieces)


@pytest.mark.parametrize('failure', ['none', 'auth', 'timeout', 'reject', 'partial'])
def test_real_store_sender_outcomes(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('FEISHU_APP_ID', 'id')
    monkeypatch.setenv('FEISHU_APP_SECRET', 'secret')
    calls = []
    def api(path, payload, token=None):
        calls.append((path, payload))
        if path.startswith('auth'):
            return {'code': 1} if failure == 'auth' else {'code': 0, 'tenant_access_token': 'test'}
        if failure == 'timeout': raise TimeoutError()
        if failure == 'reject' or (failure == 'partial' and len(calls) == 3): return {'code': 999}
        return {'code': 0, 'data': {'message_id': 'm' + str(len(calls))}}
    store = d.DetailStore(tmp_path)
    store.enqueue(request(body='x' * 5000), ['feishu:oc_group'])
    sender = d.Sender(tmp_path, store, call=api)
    assert store.drain_one(sender)
    expected = {'none':'sent', 'auth':'failed', 'timeout':'unknown', 'reject':'failed', 'partial':'unknown'}
    assert store.status()[0]['status'] == expected[failure]
    before = len(calls)
    store.recover()
    assert not store.drain_one(sender)
    assert len(calls) == before
    if failure == 'none':
        assert len(calls) == 3
        assert calls[1][1]['uuid'] != calls[2][1]['uuid']
        with store.connect() as db:
            assert [r[0] for r in db.execute('SELECT status FROM parts')] == ['sent','sent']


def test_email_dispatch_preserved(tmp_path):
    store = d.DetailStore(tmp_path)
    store.enqueue(request(), ['recipient@example.com'])
    sent = []
    sender = d.Sender(tmp_path, store, call=lambda *a: pytest.fail('Feishu called'),
                      mail_factory=lambda home: (lambda job, r: (sent.append(r['address']) or ('sent',None,False)), None))
    assert store.drain_one(sender)
    assert sent == ['recipient@example.com']


def test_interrupted_job_not_replayed(tmp_path):
    store = d.DetailStore(tmp_path)
    store.enqueue(request(), ['feishu:oc_one'])
    with store.connect() as db:
        db.execute("UPDATE jobs SET status='sending'")
        db.execute("UPDATE recipients SET status='sending'")
    store.recover()
    assert store.status()[0]['status'] == 'unknown'
    assert not store.drain_one(lambda *a: pytest.fail('replayed'))


def test_socket_real_enqueue(tmp_path):
    store = d.DetailStore(tmp_path/'queue')
    path = tmp_path/'service.sock'
    with d.Server(path, store, lambda: ['feishu:oc_one']) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.connect(str(path)); sock.sendall((json.dumps(request())+'\n').encode())
                result = json.loads(sock.makefile('rb').readline())
            assert result['status'] == 'queued' and result['channel'] == 'feishu'
        finally:
            server.shutdown(); thread.join()


def test_plugin_closed_turn_does_not_enqueue(monkeypatch):
    import types
    relay = types.ModuleType('agent.relay_runtime')
    relay.current_turn = lambda: types.SimpleNamespace(closed=True, lease=None)
    monkeypatch.setitem(sys.modules, 'agent.relay_runtime', relay)
    spec = importlib.util.spec_from_file_location('detail_plugin_test', ROOT/'demo-runtime/plugins/cli/email-queue/__init__.py')
    plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
    assert json.loads(plugin.submit({'title':'t','content':'c'}, _details=True))['status'] == 'rejected'
