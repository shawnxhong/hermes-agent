import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_interactive_releases_background_and_inherits_terminal(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/hermes-box-voice.py'
    spec = importlib.util.spec_from_file_location('box_interactive', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module, 'control', lambda action: calls.append(action))
    def run(command, **kwargs):
        assert calls == ['stop']
        assert command[1:] == ['--cli']
        assert kwargs['env']['HERMES_CLI_VOICE_AUTO_START'] == '1'
        assert 'stdin' not in kwargs and 'stdout' not in kwargs
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert module.run_interactive() == 0
