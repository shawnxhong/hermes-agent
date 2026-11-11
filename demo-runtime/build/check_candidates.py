#!/usr/bin/env python3
"""Read-only import/package/GPU checks in isolated candidate containers."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from render_profile import ROOT, read_json


def run(image,python,code,devices=False):
    cmd=['docker','run','--pull=never','--rm','--network','none','--memory','2g']
    if devices:
        cmd += ['--device','/dev/dri']
        for group in sorted({p.stat().st_gid for p in Path('/dev/dri').glob('*')}):cmd += ['--group-add',str(group)]
        cmd += ['-e','OCL_ICD_VENDORS=/opt/hermes/gpu-runtime/vendors','-e','LD_LIBRARY_PATH=/opt/hermes/gpu-runtime/driver']
    return subprocess.run(cmd+['--entrypoint',python,image,'-c',code],capture_output=True,text=True,timeout=90)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--roles-build',type=Path,required=True);p.add_argument('--wake-build',type=Path,required=True);p.add_argument('--gpu',action='store_true');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    lock=read_json(ROOT/'locks/python-build.json');roles=read_json(a.roles_build/'receipt.json');wake=read_json(a.wake_build/'receipt.json');results={'deployed':False,'microphone_used':False,'model_inference':False,'roles':{}}
    if set(roles['roles'])!={'cli','gateway','services','mail'}:
        raise ValueError('All four role builds are required for candidate checks')
    for role,record in roles['roles'].items():
        if record.get('returncode') or record.get('import_smoke_returncode')!=0:raise ValueError('Role smoke missing or failed')
        checks={}
        for env,packages in lock['environments'].items():
            code="import importlib.metadata as m,json; print(json.dumps({d.metadata['Name'].lower().replace('_','-').replace('.','-'):d.version for d in m.distributions()}))"
            r=run(record['image'],f'/opt/hermes/{env}/bin/python',code)
            if r.returncode:raise RuntimeError('Package inventory failed')
            actual=json.loads(r.stdout);actual.pop('hermes-agent',None);expected={x['name']:x['version'] for x in packages}
            if actual!=expected:raise ValueError(f'{role}/{env} package mismatch: '+str(sorted(set(actual.items())^set(expected.items()))))
            checks[env]={'package_parity':True,'count':len(actual)}
        results['roles'][role]={'image':record['image'],'environments':checks}
    if a.gpu:
        image=roles['roles']['services']['image'];r=run(image,'/opt/hermes/asr-env/bin/python',"import openvino as ov,json; print(json.dumps(ov.Core().available_devices))",True)
        if r.returncode:raise RuntimeError('GPU enumeration failed: '+r.stderr[-1000:])
        devices=json.loads(r.stdout);results['asr_devices']=devices
        if not any(d.startswith('GPU') for d in devices):raise ValueError('No GPU enumerated')
    if wake.get('smoke_returncode')!=0:raise ValueError('Wake smoke missing or failed')
    results['wake']={'image':wake['image'],'bridge_and_aec_load':True}
    with a.output.open('x') as stream:json.dump(results,stream,indent=2);stream.write('\n')
    print('All candidate package inventories match; optional GPU check passed. No deployment.')

if __name__=='__main__':main()
