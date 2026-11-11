#!/usr/bin/env python3
"""Build authorized AISP sources and the Hermes bridge offline; no deployment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import zipfile
from render_profile import ROOT, read_json
from prepare_assembly import safe_path


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            safe_path(destination,item.filename)
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Archive symlinks are not supported')
        z.extractall(destination)


def verify_tree(directory, records):
    expected=set(records)
    actual={str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() or p.is_symlink()}
    if actual!=expected:raise ValueError('Dependency file inventory mismatch')
    for name,record in records.items():
        path=safe_path(directory,name)
        if 'link' in record:
            if not path.is_symlink() or str(path.readlink())!=record['link']:
                raise ValueError('Dependency link mismatch')
            continue
        if path.stat().st_size!=record.get('bytes', record.get('size')) or sha(path)!=record['sha256']:
            raise ValueError('Dependency hash mismatch')


def build(args):
    lock=read_json(ROOT/'locks/sdk-build.json')
    if args.toolchain_build:
        from build_os import image_from_receipt
        lock['toolchain_image']=image_from_receipt(args.toolchain_build,'sdk-toolchain')
    if sha(args.sdk_source)!=lock['sdk_archive_sha256'] or sha(args.libpng)!=lock['libpng']['sha256']:
        raise ValueError('Source archive hash mismatch')
    for name, record in lock['dependencies'].items():
        verify_tree(args.dependencies/name,record['files'])
    subprocess.run(['docker','image','inspect',lock['toolchain_image']],stdout=subprocess.DEVNULL,check=True)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    extract_zip(args.sdk_source,out/'source')
    (out/'dependencies').mkdir()
    for name in lock['dependencies']:
        shutil.copytree(args.dependencies/name,out/'dependencies'/name,symlinks=True)
    extract_zip(args.libpng,out/'png')
    shutil.move(str(out/'png/libpng-1.6.43'),out/'dependencies/libpng')
    shutil.copy2(ROOT/'wake/bridge.cpp',out/'bridge.cpp')
    prefix=['docker','run','--pull=never','--rm','--network','none','--cpus',str(args.jobs),'--memory','8g',
            '--user',f'{os.getuid()}:{os.getgid()}','-e','HOME=/work','-v',f'{out}:/work',
            '--entrypoint','/bin/sh',lock['toolchain_image']]
    configure=['cmake','-S','/work/source','-B','/work/build','-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
               '-DBUILD_SAMPLES=OFF','-DBUILD_TESTING=OFF','-DBUILD_PYTHON_BINDINGS=OFF',
               '-DFETCHCONTENT_FULLY_DISCONNECTED=ON','-DFETCHCONTENT_UPDATES_DISCONNECTED=ON',
               '-DCMAKE_INSTALL_PREFIX=/work/install']
    configure += [f'-DFETCHCONTENT_SOURCE_DIR_{n.upper()}=/work/dependencies/{n}' for n in [*lock['dependencies'],'libpng']]
    commands=[configure,['cmake','--build','/work/build','--parallel',str(args.jobs)],
              ['cmake','--install','/work/build'],
              ['g++','-std=c++20','-O2','-fPIC','-shared','/work/bridge.cpp',
               '-I/work/install/runtime/include','-I/work/install/runtime/3rdparty/openvino/include',
               '-L/work/install/runtime/bin/intel64/Release','-L/work/install/runtime/3rdparty/openvino/lib',
               '-laisp_runtime','-lopenvino','-o','/work/libwake.so']]
    receipt={'network':'none','toolchain_image':lock['toolchain_image'],'sdk_source_sha256':sha(args.sdk_source),
             'bridge_source_sha256':sha(out/'bridge.cpp'),'lock_sha256':sha(ROOT/'locks/sdk-build.json'),
             'deployed':False,'stages':[]}
    for i,command in enumerate(commands):
        with (out/f'{i}.log').open('w') as log:
            result=subprocess.run(prefix+['-c','exec "$@"','native-build',*command],stdout=log,stderr=subprocess.STDOUT)
        receipt['stages'].append({'command':command,'returncode':result.returncode})
        (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print('native stage',i,'returncode',result.returncode,flush=True)
        if result.returncode:raise RuntimeError('Build failed; inspect stage log')
    receipt['sdk_output_sha256']=sha(out/'install/runtime/bin/intel64/Release/libaisp_runtime.so')
    receipt['bridge_output_sha256']=sha(out/'libwake.so');receipt['completed']=True
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk-source',type=Path,required=True);p.add_argument('--dependencies',type=Path,required=True)
    p.add_argument('--libpng',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--toolchain-build',type=Path)
    p.add_argument('--jobs',type=int,default=2)
    a=p.parse_args()
    if not 1<=a.jobs<=8:p.error('jobs must be between 1 and 8')
    build(a)

if __name__=='__main__':main()
