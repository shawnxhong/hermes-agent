import importlib.util
import json
from pathlib import Path
import threading
from urllib.request import Request, build_opener, ProxyHandler

import pytest

ROOT = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/plugins/demo-home'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sim = load('demo_sim', 'simulator.py')
plugin = load('demo_plugin', '__init__.py')


def test_persistence_idempotence_validation_and_reset(tmp_path):
    db = tmp_path / 'state.db'
    store = sim.Store(db)
    initial = store.status()
    assert len(initial['devices']) == 6
    assert all(set(d) == {'id', 'name', 'state'} and d['name'] for d in initial['devices'])
    result = store.set(['living_room_tv', 'lights'], 'off')
    assert set(result['changed_ids']) == {'living_room_tv', 'lights'}
    assert sim.Store(db).status()['devices'] == result['devices']
    assert store.set(['lights'], 'off')['changed_ids'] == []
    with pytest.raises(ValueError):
        store.set(['lights', 'not_real'], 'on')
    assert store.status()['devices'] == result['devices']
    assert store.reset()['devices'] == initial['devices']


def test_migrates_legacy_database_without_losing_state(tmp_path):
    db = tmp_path / 'old.db'
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE devices (id TEXT PRIMARY KEY, name_en TEXT, name_zh TEXT, state TEXT)')
        conn.execute('INSERT INTO devices VALUES (?,?,?,?)',
                     ('lights', 'Lights', 'legacy-name', 'off'))
    status = sim.Store(db).status()
    lights = next(item for item in status['devices'] if item['id'] == 'lights')
    assert lights == {'id': 'lights', 'name': 'Lights', 'state': 'off'}


def test_real_http_plugin_and_failure(tmp_path, monkeypatch):
    server = sim.make_server(sim.Store(tmp_path / 'state.db'), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    try:
        status = json.loads(plugin.request('/devices', port=port))
        ids = [d['id'] for d in status['devices']]
        result = json.loads(plugin.request('/devices/set', {'device_ids': ids, 'state': 'off'}, port))
        assert result['success'] and all(d['state'] == 'off' for d in result['devices'])
        assert not json.loads(plugin.request('/devices/set', {'device_ids': ['bad'], 'state': 'off'}, port))['success']
        req = Request(f'http://127.0.0.1:{port}/devices/set', data=b'{}',
                      headers={'Origin': 'https://example.com', 'Content-Type': 'application/json'})
        from urllib.error import HTTPError
        with pytest.raises(HTTPError) as exc:
            build_opener(ProxyHandler({})).open(req, timeout=2)
        assert exc.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
    assert not json.loads(plugin.request('/devices', port=port))['success']
