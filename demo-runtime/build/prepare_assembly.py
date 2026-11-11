#!/usr/bin/env python3
"""Validate/materialize role sources or render Compose, without starting services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from render_profile import ROOT, read_json, write_new


def safe_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Unsafe manifest path')
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Manifest path escapes source root')
    return target


def validate_sources(root=ROOT):
    roles = read_json(root / 'assembly/roles.json')['roles']
    count = 0
    for role in roles.values():
        for section in ('core_overrides', 'plugins', 'container_helpers'):
            for relative, record in role[section].items():
                safe_path(root, relative)
                source = safe_path(root.parent, record['source'])
                if hashlib.sha256(source.read_bytes()).hexdigest() != record['sha256']:
                    raise ValueError('Role source checksum mismatch')
                count += 1
        for relative in role['core_remove']:
            safe_path(root, relative)
    return count


def materialize(role, output, root=ROOT):
    validate_sources(root)
    spec = read_json(root / 'assembly/roles.json')['roles'][role]
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists')
    # Only Git-tracked sources; no .env, caches or untracked developer files.
    names = subprocess.check_output(['git', '-C', str(root.parent), 'ls-files', '-z'], text=True).split('\0')
    stage = Path(tempfile.mkdtemp(prefix='.hermes-assembly-', dir=output.parent))
    try:
        for name in names:
            if not name or name.startswith('demo-runtime/') or name in spec['core_remove']:
                continue
            src = safe_path(root.parent, name)
            dst = safe_path(stage / 'app', name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        for section, folder in [('core_overrides','app'),('plugins','plugins'),('container_helpers','container')]:
            for name, record in spec[section].items():
                dst = safe_path(stage / folder, name)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(safe_path(root.parent, record['source']), dst)
        (stage / 'ASSEMBLY.json').write_text(json.dumps({'role':role,'reference_image':spec['accepted_image_id'],'git_commit':subprocess.check_output(['git','-C',str(root.parent),'rev-parse','HEAD'],text=True).strip()},indent=2)+'\n')
        # mkdir claims destination exclusively; never replace an existing tree.
        output.mkdir(mode=0o700)
        for child in stage.iterdir():
            shutil.move(str(child), output / child.name)
    finally:
        shutil.rmtree(stage)


def render_compose(bindings, root=ROOT):
    template = read_json(root / 'deploy/compose.template.json')
    text = json.dumps(template)
    required = set(re.findall(r'\$\{([A-Z_]+)\}', text))
    if not isinstance(bindings, dict) or set(bindings) != required:
        raise ValueError('Host bindings must exactly match template')
    for key, value in bindings.items():
        if not isinstance(value, str) or not value or 'REPLACE_ME' in value or '$' in value:
            raise ValueError('Invalid host binding')
        if key.endswith('GID') or key == 'UID':
            if not value.isdecimal(): raise ValueError('Numeric identity required')
        elif key == 'PROJECT':
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',value):raise ValueError('Invalid project name')
        elif not Path(value).is_absolute():
            raise ValueError('Host paths must be absolute')
    def replace(value):
        if isinstance(value,str):
            return re.sub(r'\$\{([A-Z_]+)\}',lambda m:bindings[m[1]],value)
        if isinstance(value,list):return [replace(v) for v in value]
        if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
        return value
    return replace(template)


def discover_fan(sys_root=Path('/sys')):
    controls = [p.resolve() for p in (sys_root/'class/thermal').glob('cooling_device*')
                if (p/'type').is_file() and (p/'type').read_text().strip() == 'TFN1']
    rpms = [(p/'fan1_input').resolve() for p in (sys_root/'class/hwmon').glob('*')
            if (p/'name').is_file() and (p/'name').read_text().strip() == 'acpi_fan'
            and (p/'fan1_input').is_file()]
    temps = sys_root/'devices/platform/coretemp.0'
    if len(controls)!=1 or len(rpms)!=1 or not temps.is_dir():
        raise ValueError('Fan hardware ambiguous or unsupported; supply reviewed bindings')
    return {'FAN_CONTROL':str(controls[0]),'FAN_RPM':str(rpms[0]),'TEMPERATURES':str(temps)}


def verify_assets(manifest, directory):
    for relative, record in read_json(manifest).items():
        path = safe_path(directory, relative)
        if not path.is_file() or path.stat().st_size != record['size']:
            raise ValueError('Asset missing or wrong size')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(8*1024*1024),b''):digest.update(block)
        if digest.hexdigest()!=record['sha256']:raise ValueError('Asset checksum mismatch')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('check-sources')
    sub.add_parser('discover-fan')
    role=sub.add_parser('role');role.add_argument('--role',choices=['cli','gateway','services','mail'],required=True);role.add_argument('--output',type=Path,required=True)
    compose=sub.add_parser('compose');compose.add_argument('--bindings',type=Path,required=True);compose.add_argument('--output',type=Path,required=True)
    asset=sub.add_parser('verify-assets');asset.add_argument('--manifest',type=Path,required=True);asset.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='check-sources': print('Verified role source records:',validate_sources())
    elif args.action=='discover-fan':print(json.dumps(discover_fan(),indent=2))
    elif args.action=='role':materialize(args.role,args.output)
    elif args.action=='compose':write_new(args.output,render_compose(read_json(args.bindings)))
    elif args.action=='verify-assets':verify_assets(args.manifest,args.directory);print('Asset hashes verified.')


if __name__=='__main__':main()
