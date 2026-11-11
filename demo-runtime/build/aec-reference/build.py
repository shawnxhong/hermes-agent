#!/usr/bin/env python3
"""Build an offline dependency overlay from committed recipe + verified cached inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    recipe = Path(__file__).resolve().parent
    repo = recipe.parents[1]
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain', '--', str(recipe)], text=True):
        raise SystemExit('Commit recipe changes before building')
    lock = json.loads((recipe / 'inputs.lock.json').read_text())
    for name, expected in lock['inputs'].items():
        if hashlib.sha256((args.cache / name).read_bytes()).hexdigest() != expected:
            raise SystemExit(f'Input hash mismatch: {name}')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    context = out / 'context'
    context.mkdir()
    for name in lock['inputs']:
        if name.endswith('.deb'):
            run('dpkg-deb', '-x', str(args.cache / name), str(out / 'headers'))
    shutil.copytree(out / 'headers/usr/include', context / 'include')
    extra = context / 'include/spa/plugins/audioconvert'
    extra.mkdir(parents=True)
    shutil.copy2(args.cache / 'wavfile.h', extra / 'wavfile.h')
    shutil.copy2(args.cache / 'echo-cancel-1.6.2.c', context / 'module-echo-cancel.c')
    run('patch', '--batch', '--fuzz=0', '-p1', '-i', str(recipe / 'quiesce-before-destroy.patch'), cwd=context)
    (context / 'config.h').write_text('#define PACKAGE_VERSION "1.6.2"\n#define _GNU_SOURCE 1\n')
    shutil.copy2(recipe / 'Dockerfile', context / 'Dockerfile')
    provenance = {**lock, 'ops_commit': commit, 'patch_sha256': hashlib.sha256((recipe / 'quiesce-before-destroy.patch').read_bytes()).hexdigest()}
    (context / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    run('docker', 'build', '--network=none', '--pull=false', '--build-arg', 'PARENT=' + lock['parent'], '-t', args.tag, str(context))
    image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.tag], text=True).strip()
    (out / 'build.json').write_text(json.dumps({**provenance, 'image': image, 'tag': args.tag}, indent=2) + '\n')
    print(image)


if __name__ == '__main__':
    main()
