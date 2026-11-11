#!/usr/bin/env python3
"""Build role candidates from source + new Python deps + hash-locked binary assets."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from render_profile import ROOT, read_json
from prepare_assembly import materialize
from build_native import sha, verify_tree


def build(args):
    lock=read_json(ROOT/'locks/python-build.json')
    python_receipt=read_json(args.python_build/'receipt.json')
    if python_receipt['returncode'] or python_receipt['lock_sha256']!=sha(ROOT/'locks/python-build.json'):
        raise ValueError('Unverified Python build')
    pip_wheel=next(r for r in lock['environments']['tts-env'] if r['name']=='pip')
    assets=read_json(ROOT/'locks/runtime-assets.json');verify_tree(args.assets,assets['files'])
    args.output.mkdir(parents=True,exist_ok=False)
    receipt={'deployed':False,'network':'none','python_image':python_receipt['image'],'roles':{}}
    for role in args.roles:
        out=args.output/role;out.mkdir();context=out/'context'
        materialize(role,context)
        shutil.copytree(args.assets,context/'assets',symlinks=True)
        profile='gateway-state' if role=='gateway' else 'state'
        shutil.copytree(ROOT/profile/'skills',context/'skills')
        shutil.copy2(ROOT/f'{profile}-SOUL.md',context/'SOUL.md')
        recipe=f'''FROM {python_receipt['image']} AS python_deps
FROM {python_receipt['os_image']}
COPY --from=python_deps /opt/hermes /opt/hermes
COPY assets/ /
COPY app /opt/hermes/app
COPY plugins /opt/hermes/plugins
COPY container /opt/hermes/container
COPY skills /opt/hermes/skills
COPY SOUL.md /opt/hermes/SOUL.md
COPY --from=python_deps /wheels/{pip_wheel['sha256']}/{Path(pip_wheel['file']).name} /build-pip.whl
ENV PATH="/opt/hermes/core-env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" HOME="/home/hermes" HERMES_HOME="/state" PYTHONUNBUFFERED="1" HF_HUB_OFFLINE="1" HF_HUB_DISABLE_TELEMETRY="1" NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1"
RUN PYTHONPATH=/build-pip.whl /opt/hermes/core-env/bin/python -m pip install --no-index --no-deps --no-build-isolation -e /opt/hermes/app && rm /build-pip.whl && ln -s /opt/hermes/core-env /opt/hermes/app/venv && mkdir -p /state /ipc /home/hermes /models/asr /models/tts && chmod -R a+rX /opt/hermes/app /opt/hermes/plugins /opt/hermes/container /opt/hermes/skills
RUN test -f /opt/hermes/core-env/lib/python3.11/site-packages/onnxruntime/capi/libonnxruntime.so.1.27.0 && ln -sf ../../onnxruntime/capi/libonnxruntime.so.1.27.0 /opt/hermes/core-env/lib/python3.11/site-packages/sherpa_onnx/lib/libonnxruntime.so
WORKDIR /home/hermes
USER 1000:1000
ENTRYPOINT ["/opt/hermes/core-env/bin/python", "/opt/hermes/container/entrypoint.py"]
CMD ["check"]
'''
        (context/'Dockerfile').write_text(recipe)
        with (out/'build.log').open('w') as log:
            r=subprocess.run(['docker','build','--network=none','--pull=false','--iidfile',str(out/'image.id'),str(context)],stdout=log,stderr=subprocess.STDOUT)
        entry={'returncode':r.returncode,'recipe_sha256':sha(context/'Dockerfile')}
        if not r.returncode:entry['image']=(out/'image.id').read_text().strip()
        receipt['roles'][role]=entry;(args.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(role,'build returncode',r.returncode,flush=True)
        if r.returncode:raise RuntimeError('Role build failed; inspect build.log')
        with (out/'smoke.log').open('w') as log:
            smoke=subprocess.run(['docker','run','--pull=never','--rm','--network','none','--memory','2g',entry['image'],'check'],stdout=log,stderr=subprocess.STDOUT)
        entry['import_smoke_returncode']=smoke.returncode
        (args.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(role,'import smoke returncode',smoke.returncode,flush=True)
        if smoke.returncode:raise RuntimeError('Role smoke failed; inspect smoke.log')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--python-build',type=Path,required=True);p.add_argument('--assets',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--roles',nargs='+',choices=['cli','gateway','services','mail'],default=['cli','gateway','services','mail'])
    a=p.parse_args();a.output=a.output.resolve();build(a)

if __name__=='__main__':main()
