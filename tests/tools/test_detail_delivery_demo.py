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


def test_startup_snapshot_and_invalid_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('FEISHU_APP_ID', 'test-id')
    monkeypatch.setenv('FEISHU_APP_SECRET', 'test-secret')
    config = tmp_path/'config.yaml'
    config.write_text('detail_delivery: {channel: feishu, feishu: {target_id: oc_one}}')
    targets, sender, channel = d.startup(tmp_path, d.DetailStore(tmp_path/'queue'))
    assert targets == ('feishu:oc_one',) and channel == 'feishu'
    config.write_text('detail_delivery: {channel: feishu, feishu: {target_id: oc_two}}')
    assert targets == ('feishu:oc_one',)
    assert d.startup(tmp_path, d.DetailStore(tmp_path/'queue'))[0] == ('feishu:oc_two',)
    assert sender({}, {'address':'recipient@example.com'}) == ('failed', 'inactive_delivery_target', True)
    monkeypatch.delenv('FEISHU_APP_SECRET')
    with pytest.raises(ValueError): d.settings(tmp_path)
    config.write_text('detail_delivery: {channel: unsupported}')
    with pytest.raises(ValueError): d.settings(tmp_path)


def test_default_feishu_and_restart_email(tmp_path, monkeypatch):
    monkeypatch.setenv('FEISHU_APP_ID', 'id')
    monkeypatch.setenv('FEISHU_APP_SECRET', 'secret')
    config = tmp_path/'config.yaml'
    config.write_text('detail_delivery: {feishu: {target_id: oc_one}}')
    store = d.DetailStore(tmp_path/'queue')
    targets, _, channel = d.startup(tmp_path, store)
    assert channel == 'feishu'
    config.write_text('detail_delivery: {channel: email}')
    calls = []
    def factory(home):
        return lambda job, r: (calls.append(r['address']) or ('sent',None,False)), lambda: ['a@example.com']
    monkeypatch.setattr(d, 'production_settings', factory)
    new_targets, sender, channel = d.startup(tmp_path, store, mail_factory=factory)
    assert targets == ('feishu:oc_one',) and new_targets == ('a@example.com',)
    assert channel == 'email'
    store.enqueue(request(), new_targets)
    store.drain_one(sender)
    assert calls == ['a@example.com']


def test_pending_other_target_blocks_startup(tmp_path, monkeypatch):
    monkeypatch.setenv('FEISHU_APP_ID', 'id')
    monkeypatch.setenv('FEISHU_APP_SECRET', 'secret')
    (tmp_path/'config.yaml').write_text('detail_delivery: {feishu: {target_id: oc_one}}')
    store = d.DetailStore(tmp_path/'queue')
    store.enqueue(request(), ['old@example.com'])
    with pytest.raises(ValueError, match='Pending details'):
        d.startup(tmp_path, store)
    assert store.status()[0]['status'] == 'queued'


def test_request_cannot_override_channel(tmp_path):
    store = d.DetailStore(tmp_path/'queue')
    with pytest.raises(ValueError):
        store.enqueue({**request(), 'channel':'email'}, ['feishu:oc_one'])
    assert store.status() == []


def test_only_one_tool_and_startup_channel_description(tmp_path, monkeypatch):
    import hermes_constants
    monkeypatch.setattr(hermes_constants, 'get_hermes_home', lambda: tmp_path)
    (tmp_path/'detail-queue').mkdir()
    (tmp_path/'detail-queue/active.json').write_text('{"channel":"feishu"}')
    (tmp_path/'config.yaml').write_text('detail_delivery: {channel: email}')
    spec = importlib.util.spec_from_file_location('single_detail_plugin', ROOT/'demo-runtime/plugins/cli/email-queue/__init__.py')
    plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
    tools = []
    class Context:
        def register_tool(self, **kw): tools.append(kw)
    plugin.register(Context())
    assert [t['name'] for t in tools] == ['deliver_details']
    assert 'through feishu' in tools[0]['schema']['description']
    assert set(tools[0]['schema']['parameters']['properties']) == {'title','content'}


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


def test_socket_frozen(tmp_path, monkeypatch):
    monkeypatch.setenv('FEISHU_APP_ID', 'id')
    monkeypatch.setenv('FEISHU_APP_SECRET', 'secret')
    config = tmp_path/'config.yaml'
    config.write_text('detail_delivery: {feishu: {target_id: oc_one}}')
    store = d.DetailStore(tmp_path/'queue')
    addresses, _, _ = d.startup(tmp_path, store)
    path = tmp_path/'service.sock'
    with d.Server(path, store, lambda: addresses) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            config.write_text('detail_delivery: {channel: email}')
            for i in range(2):
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.connect(str(path))
                    sock.sendall((json.dumps(request(str(i)))+'\n').encode())
                    result = json.loads(sock.makefile('rb').readline())
                assert result['channel'] == 'feishu'
            with store.connect() as db:
                assert {r[0] for r in db.execute('SELECT address FROM recipients')} == {'feishu:oc_one'}
        finally:
            server.shutdown(); thread.join()


def test_plugin_closed_turn_does_not_enqueue(monkeypatch):
    import types
    relay = types.ModuleType('agent.relay_runtime')
    relay.current_turn = lambda: types.SimpleNamespace(closed=True, lease=None)
    monkeypatch.setitem(sys.modules, 'agent.relay_runtime', relay)
    spec = importlib.util.spec_from_file_location('detail_plugin_test', ROOT/'demo-runtime/plugins/cli/email-queue/__init__.py')
    plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
    assert json.loads(plugin.submit({'title':'t','content':'c'}))['status'] == 'rejected'


@pytest.mark.parametrize('stage', ['auth', 'send'])
@pytest.mark.parametrize('kind', ['timeout', 'http', 'rejected', 'bad_json'])
def test_failure_metadata_only(tmp_path, monkeypatch, capsys, stage, kind):
    import urllib.error
    monkeypatch.setenv('FEISHU_APP_ID', 'PRIVATE_ID')
    monkeypatch.setenv('FEISHU_APP_SECRET', 'PRIVATE_SECRET')
    calls = []
    def api(path, payload, token=None):
        calls.append(path)
        if stage == 'send' and path.startswith('auth'):
            return {'code':0, 'tenant_access_token':'PRIVATE_TOKEN'}
        if kind == 'timeout': raise urllib.error.URLError(TimeoutError('PRIVATE_SECRET'))
        if kind == 'http': raise urllib.error.HTTPError('https://PRIVATE_URL', 503, 'PRIVATE_SECRET', {}, None)
        if kind == 'rejected': return {'code':999, 'msg':'PRIVATE_BODY'}
        raise json.JSONDecodeError('PRIVATE_SECRET', 'PRIVATE_BODY', 0)
    store = d.DetailStore(tmp_path/'queue')
    job = store.enqueue(request(body='PRIVATE_BODY'), ['feishu:oc_private'])
    store.drain_one(d.Sender(tmp_path, store, call=api))
    log = capsys.readouterr().err
    assert 'job='+job['job_id'] in log and 'stage='+stage in log
    assert 'elapsed_ms=' in log and 'PRIVATE' not in log and 'oc_private' not in log
    if kind == 'http': assert 'http_status=503' in log
    if kind == 'rejected': assert 'api_code=999' in log
    if kind == 'timeout': assert 'reason_type=TimeoutError' in log
    assert len(calls) == (1 if stage == 'auth' else 2)
    assert store.status()[0]['status'] == ('failed' if stage == 'auth' or kind == 'rejected' else 'unknown')


def test_logging_failure_does_not_change_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv('FEISHU_APP_ID', 'id')
    monkeypatch.setenv('FEISHU_APP_SECRET', 'secret')
    class BrokenLog:
        def write(self, text): raise OSError('full')
    monkeypatch.setattr(d.sys, 'stderr', BrokenLog())
    calls = []
    def api(*args):
        calls.append(1)
        raise TimeoutError()
    store = d.DetailStore(tmp_path/'queue')
    store.enqueue(request(), ['feishu:oc_one'])
    store.drain_one(d.Sender(tmp_path, store, call=api))
    assert store.status()[0]['status'] == 'failed'
    assert len(calls) == 1
