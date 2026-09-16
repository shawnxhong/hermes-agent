"""Startup validation must not race asynchronous plugin registration."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import cli
from hermes_cli import plugins


def test_unknown_names_are_rechecked_after_discovery(monkeypatch):
    known = {'terminal'}
    monkeypatch.setattr(cli, 'validate_toolset', lambda name: name in known)
    discover = Mock(side_effect=lambda: known.update({'demo_home', 'local_media'}))
    monkeypatch.setattr(plugins, 'discover_plugins', discover)
    assert cli._unknown_startup_toolsets(
        ['terminal', 'demo_home', 'local_media', 'typo', 'my_mcp'], {'my_mcp'}
    ) == ['typo']
    discover.assert_called_once_with()


def test_known_names_do_not_wait(monkeypatch):
    monkeypatch.setattr(cli, 'validate_toolset', lambda name: name == 'terminal')
    discover = Mock()
    monkeypatch.setattr(plugins, 'discover_plugins', discover)
    assert cli._unknown_startup_toolsets(['terminal', 'my_mcp'], {'my_mcp'}) == []
    discover.assert_not_called()


def test_fresh_process_loads_real_enabled_plugin(tmp_path):
    plugin = tmp_path/'plugins/startup-demo'
    plugin.mkdir(parents=True)
    (tmp_path/'config.yaml').write_text('plugins:\n  enabled: [startup-demo]\n')
    (plugin/'plugin.yaml').write_text('name: startup-demo\nversion: 0.1.0\ndescription: Test.\n')
    (plugin/'__init__.py').write_text(
        'def register(ctx):\n'
        '    ctx.register_tool(name="startup_probe", toolset="startup_probe",\n'
        '        schema={"name":"startup_probe","description":"Test.",\n'
        '                "parameters":{"type":"object","properties":{}}},\n'
        '        handler=lambda args, **kw: "ok")\n'
    )
    env = {**os.environ, 'HERMES_HOME': str(tmp_path)}
    result = subprocess.run([sys.executable, '-c',
        'from cli import _unknown_startup_toolsets\n'
        'assert _unknown_startup_toolsets(["startup_probe", "not_real"], set()) == ["not_real"]\n'
        'from tools.registry import registry\n'
        'assert "startup_probe" in registry.get_registered_toolset_names()\n'],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
