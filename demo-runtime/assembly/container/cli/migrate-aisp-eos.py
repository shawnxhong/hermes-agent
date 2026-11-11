#!/usr/bin/env python3
"""Idempotent recording-end migration; back up only the two endpoint keys."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import yaml


def migrate(config, backup, rollback=False):
    data = yaml.safe_load(config.read_text())
    voice = data.setdefault('voice', {})
    keys = ('endpoint', 'end_phrase')
    desired = {'endpoint': {'enabled': True, 'provider': 'aisp-eos'},
               'end_phrase': {'enabled': False}}
    configured = voice.get('endpoint')
    if isinstance(configured, dict) and configured.get('provider') == 'aisp-eos':
        desired['endpoint'] = configured.copy()  # Respect an explicit enabled:false.
    if rollback:
        before = json.loads(backup.read_text())
        for key in keys:
            if key in before:
                voice[key] = before[key]
            else:
                voice.pop(key, None)
    else:
        if all(voice.get(key) == value for key, value in desired.items()):
            return
        if not backup.exists():
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump({key: voice[key] for key in keys if key in voice}, f)
        voice.update(desired)
    with tempfile.NamedTemporaryFile(mode='w', dir=config.parent, delete=False) as f:
        yaml.safe_dump(data, f, sort_keys=False)
        temp = Path(f.name)
    try:
        temp.chmod(config.stat().st_mode & 0o777)
        temp.replace(config)
    finally:
        temp.unlink(missing_ok=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('state', type=Path)
    p.add_argument('--rollback', action='store_true')
    a = p.parse_args()
    os.umask(0o077)
    migrate(a.state / 'config.yaml', a.state / 'voice-endpoint-before-aisp-eos.json', a.rollback)
    print('Endpoint config restored' if a.rollback else 'AISP EOS endpoint configuration checked')
