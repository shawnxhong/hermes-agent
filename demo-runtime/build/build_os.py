#!/usr/bin/env python3
"""Build Ubuntu dependency layers offline from locked debs, verifying package parity."""
import argparse
from build_policy import docker_build
import json
from pathlib import Path
import shutil
import subprocess
from render_profile import ROOT, read_json
from prepare_assembly import safe_path
from build_native import sha


def packages(image):
    return set(subprocess.check_output(['docker','run','--pull=never','--rm','--network','none','--entrypoint','dpkg-query',image,'-W','-f=${Package}\t${Version}\t${Architecture}\n'],text=True).splitlines())


def image_from_receipt(path,target):
    receipt=read_json(path/'receipt.json')
    if receipt.get('target')!=target or not receipt.get('passed') or receipt.get('lock_sha256')!=sha(ROOT/'locks/os-build.json'):
        raise ValueError('Unverified OS build receipt')
    return receipt['image']


def build(a):
    lock=read_json(ROOT/'locks/os-build.json')['targets'][a.target];expected=set(lock['expected_packages']);needed=expected-packages(lock['base_image'])
    assets={r['identity']:r for r in lock['available_debs']}
    if needed-assets.keys():raise ValueError('Offline deb missing from lock')
    for identity in needed:
        r=assets[identity]
        if sha(safe_path(a.materials,r['file']))!=r['sha256']:raise ValueError('Deb hash mismatch')
    a.output.mkdir(parents=True,exist_ok=False);(a.output/'debs').mkdir()
    for identity in needed:
        p=safe_path(a.materials,assets[identity]['file']);shutil.copy2(p,a.output/'debs'/p.name)
    recipe=f"FROM {lock['base_image']}\nCOPY debs /offline-debs\nRUN DEBIAN_FRONTEND=noninteractive apt-get --no-install-recommends -y install /offline-debs/*.deb && rm -r /offline-debs\n"
    (a.output/'Dockerfile').write_text(recipe)
    with (a.output/'build.log').open('w') as log:
        r=subprocess.run(docker_build(a.output/'image.id',a.output),stdout=log,stderr=subprocess.STDOUT)
    receipt={'target':a.target,'network':'none','returncode':r.returncode,'lock_sha256':sha(ROOT/'locks/os-build.json'),'passed':False}
    if not r.returncode:
        receipt['image']=(a.output/'image.id').read_text().strip();actual=packages(receipt['image']);receipt.update(missing=sorted(expected-actual),unexpected=sorted(actual-expected),passed=actual==expected)
    (a.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    if not receipt['passed']:raise RuntimeError('OS build/package parity failed; inspect receipt')
    print('OS dependency image verified:',receipt['image'],flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--target',choices=['cli','wake','sdk-toolchain'],required=True);p.add_argument('--materials',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output=a.output.resolve();build(a)

if __name__=='__main__':main()
