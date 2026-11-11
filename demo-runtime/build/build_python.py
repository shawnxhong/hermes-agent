#!/usr/bin/env python3
"""Reinstall pinned Python dependencies into new venvs in a networkless OS image."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from render_profile import ROOT, read_json
from prepare_assembly import safe_path
from build_native import sha


def build(materials, output, os_build=None):
    lock=read_json(ROOT/'locks/python-build.json')
    if os_build:
        from build_os import image_from_receipt
        lock['os_image']=image_from_receipt(os_build,'cli')
    archive=safe_path(materials,lock['interpreter']['file'])
    if sha(archive)!=lock['interpreter']['sha256']:raise ValueError('Interpreter hash mismatch')
    assets={r['file']:r for rows in lock['environments'].values() for r in rows}
    for name,r in assets.items():
        p=safe_path(materials,name)
        if p.stat().st_size!=r['bytes'] or sha(p)!=r['sha256']:raise ValueError('Wheel hash mismatch')
    output.mkdir(parents=True,exist_ok=False);context=output/'context';context.mkdir()
    with tarfile.open(archive) as tar:tar.extractall(context,filter='data')
    (context/'wheels').mkdir();(context/'requirements').mkdir()
    for name,r in assets.items():
        # Hash-named subdirectory avoids filename collisions, preserves wheel name.
        dest=context/'wheels'/r['sha256']/Path(name).name;dest.parent.mkdir(exist_ok=True);shutil.copy2(materials/name,dest)
    for env,rows in lock['environments'].items():
        (context/'requirements'/f'{env}.txt').write_text(''.join(f"/wheels/{r['sha256']}/{Path(r['file']).name} --hash=sha256:{r['sha256']}\n" for r in rows))
    pip=next(r for r in assets.values() if r['name']=='pip')
    recipe=[f"FROM {lock['os_image']}",'COPY python /opt/hermes/python','COPY wheels /wheels','COPY requirements /requirements']
    steps=[]
    for env in lock['environments']:
        steps += [f'/opt/hermes/python/bin/python3.11 -m venv --without-pip /opt/hermes/{env}',
                  f"PYTHONPATH=/wheels/{pip['sha256']}/{Path(pip['file']).name} /opt/hermes/{env}/bin/python -m pip --isolated install --no-index --no-deps --ignore-installed --require-hashes --no-compile -r /requirements/{env}.txt"]
    recipe.append('RUN '+' && '.join(steps))
    (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
    command=['docker','build','--network=none','--pull=false','--iidfile',str(output/'image.id'),str(context)]
    with (output/'build.log').open('w') as log:result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
    receipt={'network':'none','returncode':result.returncode,'os_image':lock['os_image'],'lock_sha256':sha(ROOT/'locks/python-build.json'),'recipe_sha256':sha(context/'Dockerfile'),'environment_package_counts':{k:len(v) for k,v in lock['environments'].items()},'deployed':False}
    if not result.returncode:receipt['image']=(output/'image.id').read_text().strip()
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    if result.returncode:raise RuntimeError('Python build failed; inspect build.log')
    print('Python dependency image built:',receipt['image'],flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--materials',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--os-build',type=Path);a=p.parse_args();build(a.materials.resolve(),a.output.resolve(),a.os_build)

if __name__=='__main__':main()
