"""Exercise deployed plugin variants against a real isolated IPC listener."""
import importlib.util
import json
from pathlib import Path
import socket
import threading


def test_every_channel_stop_reaches_shared_device(tmp_path, monkeypatch):
    path = tmp_path / 'service.sock'
    monkeypatch.setenv('HERMES_MEDIA_BRIDGE', str(path))
    requests = []
    errors = []
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        server.listen()
        server.settimeout(5)

        def serve():
            try:
                for _ in range(4):
                    client, _ = server.accept()
                    with client:
                        client.settimeout(5)
                        requests.append(json.loads(client.makefile('rb').readline()))
                        client.sendall(b'{"success":true,"state":"inactive","action":"stopped"}\n')
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            plugins = []
            for role in ('cli', 'gateway'):
                source = Path(__file__).resolve().parents[2] / 'demo-runtime/plugins' / role / 'demo-media/__init__.py'
                spec = importlib.util.spec_from_file_location('media_' + role, source)
                plugin = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(plugin)
                plugins.append(plugin)
            for plugin in (plugins[0], plugins[1], plugins[1], plugins[0]):
                assert plugin.execute('stop')['success']
        finally:
            worker.join(6)
        assert not worker.is_alive() and not errors
    assert [r['action'] for r in requests] == ['stop'] * 4
