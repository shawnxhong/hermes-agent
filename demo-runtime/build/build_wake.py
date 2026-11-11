#!/usr/bin/env python3
"""Build a wake candidate from rebuilt SDK/bridge and compile the AEC fix."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from render_profile import ROOT, read_json
from build_native import sha


def build(a):
    native=read_json(a.native_build/'receipt.json')
    if not native.get('completed') or native['lock_sha256']!=sha(ROOT/'locks/sdk-build.json'):
        raise ValueError('Native build is incomplete or uses a different lock')
    if native['bridge_source_sha256']!=sha(ROOT/'wake/bridge.cpp'):
        raise ValueError('Bridge source changed after native build')
    for p,expected in [(a.native_build/'install/runtime/bin/intel64/Release/libaisp_runtime.so',native['sdk_output_sha256']),
                       (a.native_build/'libwake.so',native['bridge_output_sha256'])]:
        if sha(p)!=expected:raise ValueError('Native output mismatch')
    recipe=ROOT/'build/aec-reference';lock=read_json(recipe/'inputs.lock.json')
    for name,expected in lock['inputs'].items():
        if sha(a.aec_inputs/name)!=expected:raise ValueError('AEC input mismatch')
    a.output.mkdir(parents=True,exist_ok=False);ctx=a.output/'context';ctx.mkdir()
    for name in lock['inputs']:
        if name.endswith('.deb'):
            subprocess.run(['dpkg-deb','-x',str(a.aec_inputs/name),str(a.output/'headers')],check=True)
    shutil.copytree(a.output/'headers/usr/include',ctx/'include')
    extra=ctx/'include/spa/plugins/audioconvert';extra.mkdir(parents=True)
    shutil.copy2(a.aec_inputs/'wavfile.h',extra/'wavfile.h')
    shutil.copy2(a.aec_inputs/'echo-cancel-1.6.2.c',ctx/'module-echo-cancel.c')
    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(recipe/'quiesce-before-destroy.patch')],cwd=ctx,check=True)
    (ctx/'config.h').write_text('#define PACKAGE_VERSION "1.6.2"\n#define _GNU_SOURCE 1\n')
    shutil.copytree(a.native_build/'install/runtime',ctx/'runtime',symlinks=True)
    wake=ctx/'wake';wake.mkdir();shutil.copy2(a.native_build/'libwake.so',wake/'libwake.so')
    for name in ('service.py','config.yaml','check_wake.py','check_eos.py'):shutil.copy2(ROOT/'wake'/name,wake/name)
    (wake/'tools').mkdir()
    for name in ('wake_audio.py','recording_quiet.py'):shutil.copy2(ROOT.parent/'tools'/name,wake/'tools'/name)
    shutil.copy2(ROOT/'host/recording-quiet/server.py',wake/'quiet_server.py')
    shutil.copy2(ROOT/'host/recording-quiet/recording_quiet.py',wake/'recording_quiet.py')
    # PyYAML is a pinned pure-Python wheel extraction, not copied from a product image.
    pylock=read_json(ROOT/'locks/python-build.json');yaml=next(r for r in pylock['environments']['core-env'] if r['name']=='pyyaml')
    source=a.materials/yaml['file']
    if sha(source)!=yaml['sha256']:raise ValueError('PyYAML hash mismatch')
    import zipfile
    with zipfile.ZipFile(source) as z:
        for name in z.namelist():
            if name.startswith('yaml/') and name.endswith('.py'):
                from prepare_assembly import safe_path
                target=safe_path(wake,name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
        for name in z.namelist():
            if name.endswith('/licenses/LICENSE'):(wake/'PyYAML-LICENSE').write_bytes(z.read(name));break
    os_lock=read_json(ROOT/'locks/native-platform.json')
    if a.os_build:
        from build_os import image_from_receipt
        os_lock['wake_os_image']=image_from_receipt(a.os_build,'wake')
    recipe_text=f'''FROM {native['toolchain_image']} AS aec_build
USER root
WORKDIR /build
COPY module-echo-cancel.c config.h ./
COPY include/ ./include/
RUN gcc -shared -fPIC -O2 -g -Wall -Wextra -Wno-unused-parameter -I. -Iinclude -Iinclude/pipewire-0.3 -Iinclude/spa-0.2 module-echo-cancel.c -l:libpipewire-0.3.so.0 -o libpipewire-module-echo-cancel.so
FROM {os_lock['wake_os_image']}
COPY runtime /opt/qwen-aisp/runtime
COPY wake /opt/qwen-wake
COPY --from=aec_build /build/libpipewire-module-echo-cancel.so /usr/lib/x86_64-linux-gnu/pipewire-0.3/libpipewire-module-echo-cancel.so
RUN chmod -R a+rX /opt/qwen-aisp /opt/qwen-wake
ENV LD_LIBRARY_PATH="/opt/qwen-aisp/runtime/bin/intel64/Release:/opt/qwen-aisp/runtime/3rdparty/openvino/lib" INTEL_OPENVINO_DIR="/opt/qwen-aisp"
USER 1000:1000
ENTRYPOINT ["/usr/bin/python3", "/opt/qwen-wake/service.py", "--library", "/opt/qwen-wake/libwake.so"]
'''
    (ctx/'Dockerfile').write_text(recipe_text)
    with (a.output/'build.log').open('w') as log:
        result=subprocess.run(['docker','build','--network=none','--pull=false','--iidfile',str(a.output/'image.id'),str(ctx)],stdout=log,stderr=subprocess.STDOUT)
    receipt={'deployed':False,'network':'none','returncode':result.returncode,'native':native,'wake_os_image':os_lock['wake_os_image'],'recipe_sha256':sha(ctx/'Dockerfile'),'aec_patch_sha256':sha(recipe/'quiesce-before-destroy.patch')}
    if not result.returncode:receipt['image']=(a.output/'image.id').read_text().strip()
    (a.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    if result.returncode:raise RuntimeError('Wake build failed; inspect build.log')
    smoke="import sys,ctypes; sys.path.insert(0,'/opt/qwen-wake'); from service import Native; assert Native.eos_flag('/opt/qwen-wake/libwake.so') > 0; ctypes.CDLL('/usr/lib/x86_64-linux-gnu/pipewire-0.3/libpipewire-module-echo-cancel.so'); print('SDK bridge EOS flag and AEC module load: OK; no microphone/model used')"
    with (a.output/'smoke.log').open('w') as log:
        result=subprocess.run(['docker','run','--pull=never','--rm','--network','none','--memory','2g','--entrypoint','/usr/bin/python3',receipt['image'],'-c',smoke],stdout=log,stderr=subprocess.STDOUT)
    receipt['smoke_returncode']=result.returncode
    (a.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('wake build',receipt['image'],'smoke',result.returncode,flush=True)
    if result.returncode:raise RuntimeError('Wake smoke failed; inspect smoke.log')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['native-build','aec-inputs','materials','output']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--os-build',type=Path)
    a=p.parse_args();a.output=a.output.resolve();build(a)

if __name__=='__main__':main()
