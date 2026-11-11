#!/usr/bin/env python3
"""Narrow offline config migration; never touches histories or mail queues.

Run from the candidate image, with CLI stopped. Rollback restores the three
saved keys, retaining unrelated configuration. Schema and databases unchanged.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
import yaml


def migrate(config, backup, profile, rollback=False):
    original = config.read_text()
    data = yaml.safe_load(original)
    if not rollback and data.get('voice', {}).get('endpoint', {}).get('provider') == 'aisp-eos':
        return  # The EOS image owns endpoint policy; never restore a control keyword.
    endpoint = data['voice']['end_phrase']
    if rollback:
        old = json.loads(backup.read_text())
        for key in profile:
            if key in old:
                endpoint[key] = old[key]
            else:
                endpoint.pop(key, None)
    else:
        if all(endpoint.get(k) == v for k, v in profile.items()):
            return
        with backup.open('x') as f:
            json.dump({k: endpoint[k] for k in profile if k in endpoint}, f)
        endpoint.update(profile)
    with tempfile.NamedTemporaryFile(mode='w', dir=config.parent, delete=False) as f:
        yaml.safe_dump(data, f, sort_keys=False)
        tmp = Path(f.name)
    tmp.chmod(config.stat().st_mode & 0o777)
    tmp.replace(config)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('state', type=Path)
    p.add_argument('--rollback', action='store_true')
    a = p.parse_args()
    os.umask(0o077)
    profile = json.loads(Path('/opt/hermes/voice-endpoint-profile.json').read_text())
    migrate(a.state/'config.yaml', a.state/'voice-endpoint-before-thank-you.json', profile, a.rollback)
    print('Endpoint config restored' if a.rollback else 'Endpoint config ready: thank you; spoken hint disabled')
