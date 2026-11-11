#!/usr/bin/env python3
"""Build the Ubuntu demo from a clean checkout and declared external assets."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import uuid
from build_native import sha,verify_tree
from render_profile import ROOT,read_json

REPO=ROOT.parent


def write(path,data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2)+'\n');temp.replace(path)


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def check_materials(assets):
    """Check every declared build input; no production runtime or secrets needed."""
    files={}
    for target in read_json(ROOT/'locks/os-build.json')['targets'].values():
        files.update({r['file']:r['sha256'] for r in target['available_debs']})
    py=read_json(ROOT/'locks/python-build.json')
    files[py['interpreter']['file']]=py['interpreter']['sha256']
    for packages in py['environments'].values():files.update({r['file']:r['sha256'] for r in packages})
    sdk=read_json(ROOT/'locks/sdk-build.json')
    files.update({'sdk.zip':sdk['sdk_archive_sha256'],'libpng.zip':sdk['libpng']['sha256']})
    files.update({'aec/'+name:digest for name,digest in read_json(ROOT/'build/aec-reference/inputs.lock.json')['inputs'].items()})
    trees={'runtime-assets':read_json(ROOT/'locks/runtime-assets.json')['files']}
    trees.update({'dependencies/sdk-inputs/dependencies/'+name:record['files'] for name,record in sdk['dependencies'].items()})
    missing=[name for name in files if not (assets/name).is_file()]
    missing += [name+'/'+rel for name,records in trees.items() for rel in records if not (assets/name/rel).exists() and not (assets/name/rel).is_symlink()]
    if missing:raise ValueError('Missing materials:\n'+'\n'.join(sorted(set(missing))))
    bad=[name for name,digest in files.items() if sha(assets/name)!=digest]
    for name,records in trees.items():
        try:verify_tree(assets/name,records)
        except ValueError:bad.append(name+' (tree mismatch)')
    if bad:raise ValueError('Material checksum mismatch:\n'+'\n'.join(bad))
    return fingerprint({'files':files,'trees':trees})


def required_images():
    return sorted({r['base_image'] for r in read_json(ROOT/'locks/os-build.json')['targets'].values()}|
                  {read_json(ROOT/'locks/accepted-images.json')['images']['ovms']})


def image_exists(image):
    return subprocess.run(['docker','image','inspect',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0


def validate_archive(path):
    with tarfile.open(path) as tar:
        records=json.load(tar.extractfile('index.json'))['manifests']
        if sorted(r['digest'] for r in records)!=required_images():raise ValueError('Wrong base image archive')
        for record in records:
            digest=record['digest'];raw=tar.extractfile('blobs/sha256/'+digest.split(':')[1]).read()
            if 'sha256:'+hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Corrupt image manifest')


def artifacts(path):
    record=path/'receipt.json'
    files=[record] if record.exists() else []
    if (path/'install').exists():files += [p for p in (path/'install').rglob('*') if p.is_file()]
    if (path/'libwake.so').exists():files.append(path/'libwake.so')
    return {str(p.relative_to(path)):('link:'+str(p.readlink()) if p.is_symlink() else sha(p)) for p in files}


def images_in(value):
    found=[]
    if isinstance(value,dict):
        for k,v in value.items():
            if k=='image' and isinstance(v,str):found.append(v)
            else:found+=images_in(v)
    elif isinstance(value,list):
        for v in value:found+=images_in(v)
    return sorted(set(found))


def reusable(record,key,exists=image_exists):
    if not record or record.get('status')!='passed' or record.get('key')!=key:return False
    path=Path(record['path'])
    try:
        if not record.get('artifacts') or artifacts(path)!=record['artifacts']:return False
        return all(exists(image) for image in record.get('images',[]))
    except OSError:return False


def execute(command,log):
    with log.open('w') as stream:
        proc=subprocess.Popen(list(map(str,command)),stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=proc.wait()
        except BaseException:
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=20)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            raise
    if code:raise RuntimeError(f'Stage failed ({code}); see {log}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--jobs',type=int,default=2);parser.add_argument('--memory-gb',type=int,default=8)
    parser.add_argument('--resume',action='store_true');parser.add_argument('--no-cache',action='store_true')
    parser.add_argument('--check',action='store_true');parser.add_argument('--gpu',choices=['auto','required','off'],default='auto')
    a=parser.parse_args();a.assets=a.assets.resolve();a.output=a.output.resolve()
    if not 1<=a.jobs<=8 or not 2<=a.memory_gb<=64:parser.error('jobs 1..8; memory-gb 2..64')
    if platform.machine()!='x86_64' or 'ID=ubuntu' not in Path('/etc/os-release').read_text():raise ValueError('Ubuntu x86-64 required')
    import yaml  # Required by isolated profile initialization, fail before builds.
    for tool in ['docker','git','dpkg-deb','patch']:
        if not shutil.which(tool):raise ValueError('Missing host command: '+tool)
    subprocess.run(['docker','info'],stdout=subprocess.DEVNULL,check=True)
    if subprocess.check_output(['git','-C',str(REPO),'status','--porcelain'],text=True).strip():raise ValueError('Commit/stash changes before building; clean checkout required')
    if a.output.is_relative_to(REPO) or a.output.is_relative_to(a.assets):raise ValueError('Output must be outside source and assets')
    probe=a.output
    while not probe.exists():probe=probe.parent
    if shutil.disk_usage(probe).free<30*1024**3:raise ValueError('At least 30 GiB free space required')
    commit=subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()
    print('Checking all build materials...',flush=True);material_key=check_materials(a.assets)
    missing=[image for image in required_images() if not image_exists(image)]
    if missing:validate_archive(a.assets/'images.tar')
    gpu=a.gpu=='required' or (a.gpu=='auto' and Path('/dev/dri').exists())
    if a.gpu=='required' and not Path('/dev/dri').exists():raise ValueError('GPU requested but /dev/dri missing')
    print('Preflight passed; models, credentials and a production runtime are not required.',flush=True)
    if a.check:return
    if a.output.exists() and not a.resume:raise ValueError('Output exists; use --resume or a new directory')
    a.output.mkdir(parents=True,exist_ok=True)
    with (a.output/'build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if missing:
            execute(['docker','load','-i',a.assets/'images.tar'],a.output/'image-import.log')
            if not all(image_exists(x) for x in required_images()):raise ValueError('Locked OCI IDs unavailable; check Docker containerd image store')
        state_path=a.output/'build-state.json';state=read_json(state_path) if state_path.exists() else {'stages':{}}
        policy={'jobs':a.jobs,'memory_gb':a.memory_gb,'no_cache':a.no_cache}
        os.environ['HERMES_BUILD_POLICY']=json.dumps(policy);os.environ['DOCKER_BUILDKIT']='0'
        key=fingerprint({'commit':commit,'materials':material_key,'policy':policy,'gpu':gpu})
        state.update(source_commit=commit,materials_fingerprint=material_key,policy=policy,status='building');write(state_path,state)
        paths={};upstream={}
        def stage(name,script,args):
            stage_key=fingerprint({'run':key,'name':name,'upstream':upstream})
            previous=state['stages'].get(name)
            if reusable(previous,stage_key):
                print('REUSE '+name,flush=True);path=Path(previous['path'])
            else:
                path=a.output/(name+'-'+uuid.uuid4().hex[:8]);log=path.with_suffix('.log')
                record={'status':'running','key':stage_key,'path':str(path),'log':str(log)}
                state['stages'][name]=record;write(state_path,state)
                print('BUILD '+name+' — '+str(log),flush=True)
                try:
                    execute([sys.executable,ROOT/'build'/script,*args,'--output',path],log)
                    receipt=read_json(path/'receipt.json')
                    record.update(status='passed',artifacts=artifacts(path),images=images_in(receipt))
                    if not record['artifacts']:raise ValueError('Missing stage receipt')
                except BaseException:
                    record['status']='failed';state['status']='failed';write(state_path,state);raise
                write(state_path,state)
            paths[name]=path;upstream[name]=state['stages'][name]['artifacts']
        for target in ['cli','wake','sdk-toolchain']:
            stage('os-'+target,'build_os.py',['--target',target,'--materials',a.assets])
        stage('python','build_python.py',['--materials',a.assets,'--os-build',paths['os-cli']])
        stage('native','build_native.py',['--sdk-source',a.assets/'sdk.zip','--dependencies',a.assets/'dependencies/sdk-inputs/dependencies','--libpng',a.assets/'libpng.zip','--toolchain-build',paths['os-sdk-toolchain'],'--jobs',a.jobs])
        stage('roles','build_roles.py',['--python-build',paths['python'],'--assets',a.assets/'runtime-assets'])
        stage('wake','build_wake.py',['--native-build',paths['native'],'--aec-inputs',a.assets/'aec','--materials',a.assets,'--os-build',paths['os-wake']])
        check=a.output/('checks-'+uuid.uuid4().hex[:8]+'.json')
        execute([sys.executable,ROOT/'build/check_candidates.py','--roles-build',paths['roles'],'--wake-build',paths['wake'],*(['--gpu'] if gpu else []),'--output',check],check.with_suffix('.log'))
        execute([sys.executable,ROOT/'build/check_fresh_init.py','--roles-build',paths['roles'],'--output',a.output/('init-'+uuid.uuid4().hex[:8])],a.output/'init.log')
        state.update(status='passed',checks=str(check));write(state_path,state)
        write(a.output/'release.json',{'source_commit':commit,'materials_fingerprint':material_key,'resource_policy':policy,'stages':{name:str(path) for name,path in paths.items()},'checks':read_json(check),'deployed':False})
        print('PASS — release manifest: '+str(a.output/'release.json'),flush=True)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
        print(str(e),file=sys.stderr);sys.exit(1)
    except KeyboardInterrupt:
        print('Interrupted. Logs retained; inspect outstanding Docker builds before --resume.',file=sys.stderr);sys.exit(130)
