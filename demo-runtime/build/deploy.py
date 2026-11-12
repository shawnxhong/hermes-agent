#!/usr/bin/env python3
"""Build from source, verify downloaded models, provision a new user's runtime."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from render_profile import ROOT, read_json, write_new
from prepare_assembly import verify_assets


def run(script,*args):
    subprocess.run([sys.executable,str(ROOT/'build'/script),*map(str,args)],check=True)


def install_commands(runtime,bin_dir):
    bin_dir.mkdir(parents=True,exist_ok=True)
    for name,kind in [('hermes-box-voice','voice'),('hermes-box-scene','scene'),('hermes-box-runtime','runtime')]:
        target=bin_dir/name
        payload='#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(runtime/'runtime_entry.py'))+' '+kind+' "$@"\n'
        if target.exists() or target.is_symlink():
            if target.is_file() and target.read_text()==payload:continue
            raise ValueError('Existing command belongs to another installation: '+str(target))
        with target.open('x') as f:f.write(payload)
        target.chmod(0o755)


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets',type=Path,help='Existing materials directory; default downloads into PREFIX/materials')
    p.add_argument('--git-credential',action='store_true',help='Use Git credential helper for private material download')
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--private',type=Path,required=True,help='Profile bindings and optional service credentials')
    p.add_argument('--models',type=Path,help='Default PREFIX/models; missing files downloaded from public sources')
    p.add_argument('--project',default='hermes-local');p.add_argument('--enable-gateway',action='store_true')
    p.add_argument('--jobs',default=2,type=int);p.add_argument('--memory-gb',default=8,type=int)
    p.add_argument('--install-commands',action='store_true',help='Install wrappers only in current user ~/.local/bin; refuse other installations')
    a=p.parse_args();a.prefix=a.prefix.resolve();fetch_materials=a.assets is None;a.assets=(a.assets or a.prefix/'materials').resolve();a.private=a.private.resolve()
    if a.prefix.is_relative_to(ROOT.parent):raise ValueError('Deployment must be outside checkout')
    if os.getuid()==0:raise ValueError('Run as the desktop user, not root')
    # Validate private bindings before a lengthy build; never log their values.
    from render_profile import render
    for profile in ['state','gateway-state']:render(profile,read_json(a.private/(profile+'.bindings.json')))
    running=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project='+a.project],text=True).strip()
    if running:raise ValueError('Deployment project is running; stop it explicitly before provisioning')
    a.prefix.mkdir(parents=True,exist_ok=True)
    if fetch_materials:run('fetch_materials.py','--output',a.assets,*(['--git-credential'] if a.git_credential else []))
    models=(a.models or a.prefix/'models').resolve()
    run('download_models.py','--output',models,'--runtime-assets',a.assets/'runtime-assets')
    for name in ['llm','question-asr','wake-asr','tts']:verify_assets(ROOT/'locks'/(name+'.json'),models/name)
    verify_assets(ROOT/'locks/lifecycle-audio.json',a.assets/'resources/lifecycle-audio')
    build=a.prefix/'build'
    run('build_all.py','--assets',a.assets,'--output',build,'--jobs',a.jobs,'--memory-gb',a.memory_gb,*(['--resume'] if build.exists() else []))
    release=read_json(build/'release.json');checks=a.prefix/'build-checks.json'
    if not checks.exists():write_new(checks,release['checks'])
    elif read_json(checks)!=release['checks']:raise ValueError('Build changed; use new deployment prefix')
    runtime=a.prefix/'runtime'
    if not runtime.exists():
        run('prepare_runtime.py','--private',a.private,'--output',runtime,'--models',models,'--resources',a.assets/'resources','--checks',checks,'--wake-build',release['stages']['wake'],'--project',a.project,*(['--enable-gateway'] if a.enable_gateway else []))
        shutil.copy2(ROOT/'build/runtime_entry.py',runtime/'runtime_entry.py')
        write_new(runtime/'install-provenance.json',{'source_commit':release['source_commit'],'build':str(build),'models':str(models),'assets':str(a.assets),'prepared_only':True})
    else:
        provenance=read_json(runtime/'install-provenance.json')
        if provenance['source_commit']!=release['source_commit']:raise ValueError('Existing runtime source differs; use a new prefix')
    # No network, hardware or microphone needed to verify installed profile initialization.
    for profile,role in [('state','cli'),('gateway-state','gateway')]:
        subprocess.run(['docker','run','--rm','--pull=never','--network','none','--cpus','1','--memory','2g','--user',f'{os.getuid()}:{os.getgid()}','-v',f'{runtime/profile}:/state',release['checks']['roles'][role]['image'],'init'],check=True)
    image_check=a.prefix/'installed-image-checks.json'
    if not image_check.exists():run('check_candidates.py','--roles-build',release['stages']['roles'],'--wake-build',release['stages']['wake'],'--output',image_check)
    subprocess.run([sys.executable,str(runtime/'candidate_ctl.py'),'check-prepared'],check=True)
    if a.install_commands:install_commands(runtime,Path.home()/'.local/bin')
    print('PASS: runtime prepared. No services started. Log in to the desktop, then run: '+(str(Path.home()/'.local/bin/hermes-box-voice')+' start' if a.install_commands else f'python3 {runtime}/runtime_entry.py voice start'))

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:raise SystemExit(str(e))
