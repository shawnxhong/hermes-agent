import importlib.util
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts/local-ovms/plugins/demo-media'/file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stop_escalates_and_verifies(monkeypatch):
    mod = load('host_player', 'play_media.py')
    killed = []
    clock = iter([0, .9, 1, 1.1])
    monkeypatch.setattr(mod.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(mod.subprocess, 'run', lambda cmd, **kw: killed.append(cmd))
    states = iter([{'state': 'active', 'pid': 4, 'members': ['4']},
                   {'state': 'inactive', 'pid': 0, 'members': [], 'success': True}])
    monkeypatch.setattr(mod, 'status', lambda: next(states))
    assert mod.stop_player()['success']
    assert any('--signal=SIGKILL' in cmd for cmd in killed)


def test_stop_needs_no_filename_and_no_retries(monkeypatch):
    plugin = load('media_plugin', '__init__.py')
    call = Mock(return_value={'success': True, 'state': 'inactive'})
    monkeypatch.setattr('tools.playback_control.request', call)
    assert plugin.execute('stop')['success']
    call.assert_called_once_with('stop', '')
    assert 'none' not in plugin.SCHEMA['parameters']['properties']['action']['enum']


def test_stop_does_not_claim_failure_as_success(monkeypatch):
    plugin = load('media_plugin', '__init__.py')
    monkeypatch.setattr('tools.playback_control.request', lambda *a: {'success':False,'error':'still running'})
    assert not plugin.execute('stop')['success']


def test_empty_player_stop_is_idempotent(monkeypatch):
    mod = load('host_player', 'play_media.py')
    monkeypatch.setattr(mod,'status',lambda:{'state':'inactive','pid':0,'members':[]})
    call = Mock()
    monkeypatch.setattr(mod.subprocess,'run',call)
    assert mod.stop_player()['success']
    assert mod.stop_player()['success']
    call.assert_not_called()
