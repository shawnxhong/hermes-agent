#!/usr/bin/env python3
"""Offline add-only skill migration; no configuration or database changes."""
import argparse
from pathlib import Path
import shutil

p = argparse.ArgumentParser()
p.add_argument('state', type=Path)
a = p.parse_args()
pairs = []
for name in ('healthcare', 'shopping'):
    src = Path('/opt/hermes/skills/productivity')/name/'SKILL.md'
    dest = a.state/'skills/productivity'/name/'SKILL.md'
    assert src.is_file()
    matches = list((a.state/'skills').rglob(name+'/SKILL.md'))
    assert not matches or matches == [dest], 'Conflicting skill location: '+name
    if dest.exists():
        assert dest.read_bytes() == src.read_bytes(), 'Existing skill differs: '+name
    pairs.append((src, dest))
for src, dest in pairs:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        with dest.open('xb') as out:
            out.write(src.read_bytes())
print('Panel skills ready; existing files, config and databases preserved')
