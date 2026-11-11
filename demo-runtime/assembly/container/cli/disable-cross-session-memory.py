"""Versioned non-secret behavior policy; preserve unrelated configuration."""
import json
from pathlib import Path
import sys

def apply(config):
    memory = config.setdefault('memory', {})
    memory.update(memory_enabled=False, user_profile_enabled=False, provider='')
    agent = config.setdefault('agent', {})
    disabled = agent.setdefault('disabled_toolsets', [])
    if not isinstance(disabled, list):
        raise ValueError('disabled_toolsets must be a list')
    for name in ('memory', 'session_search'):
        if name not in disabled:
            disabled.append(name)
    config.setdefault('auxiliary', {}).setdefault('background_review', {})['enabled'] = False
    return config

if __name__ == '__main__':
    # Build-time image defaults only. Existing private config is migrated with
    # the same values after stopping its writers; never copy a host config here.
    path = Path(sys.argv[1])
    config = json.loads(path.read_text())
    path.write_text(json.dumps(apply(config), indent=2) + '\n')
