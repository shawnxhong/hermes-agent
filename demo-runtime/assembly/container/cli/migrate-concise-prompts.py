#!/usr/bin/env python3
"""Explicit stopped-writer migration; backs up only the files it changes."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import tempfile
import yaml

RENAMES = {'travel-concierge': 'travel-advisor', 'demo-home-assistant': 'home-assistant'}
SKILLS = ('travel-advisor', 'home-assistant', 'healthcare', 'shopping', 'flight-search')

def configure(original):
    c = copy.deepcopy(original)
    disabled = c.setdefault('agent', {}).setdefault('disabled_toolsets', [])
    if 'tts' not in disabled:
        disabled.append('tts')
    for platform, groups in c.get('platform_toolsets', {}).items():
        if isinstance(groups, list):
            c['platform_toolsets'][platform] = [g for g in groups if g != 'tts']
    # Only translate skill identifiers, never user text, mail or history.
    def rename(value):
        if isinstance(value, str):
            return RENAMES.get(value, value)
        if isinstance(value, list):
            return [rename(v) for v in value]
        if isinstance(value, dict):
            return {RENAMES.get(k, k): rename(v) for k, v in value.items()}
        return value
    if 'skills' in c:
        c['skills'] = rename(c['skills'])
    if 'scenes' in c.get('voice', {}):
        c['voice']['scenes'] = rename(c['voice']['scenes'])
    return c

def migrate(state, source):
    config = state / 'config.yaml'
    original = yaml.safe_load(config.read_text())
    updated = configure(original)
    assets = {'SOUL.md': source / 'SOUL.md'}
    for name in SKILLS:
        rel = f'skills/productivity/{name}/SKILL.md'
        assets[rel] = source / rel
    # Validate all payloads before changing any state.
    for path in assets.values():
        assert path.is_file(), path
    legacy = [state / 'skills/productivity' / name for name in RENAMES]
    backup_root = state / 'backups'
    backup_root.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='concise-prompts-', dir=backup_root))
    for rel in ('config.yaml', *assets):
        p = state / rel
        if p.exists():
            dest = backup / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    for p in legacy:
        if p.exists():
            dest = backup / p.relative_to(state)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), dest)
    for rel, src in assets.items():
        dest = state / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    config.write_text(yaml.safe_dump(updated, sort_keys=False))
    config.chmod(0o600)
    print('Migrated profile; rollback assets:', backup)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('state', type=Path)
    p.add_argument('--source', type=Path, default=Path('/opt/hermes'))
    p.add_argument('--defaults', action='store_true')
    a = p.parse_args()
    if a.defaults:
        a.state.write_text(json.dumps(configure(json.loads(a.state.read_text())), indent=2)+'\n')
    else:
        migrate(a.state, a.source)
