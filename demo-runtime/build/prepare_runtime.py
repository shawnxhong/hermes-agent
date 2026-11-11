#!/usr/bin/env python3
"""Prepare a private candidate runtime without starting or changing services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import yaml
from render_profile import ROOT, read_json, render, write_new
from prepare_assembly import render_compose, discover_fan


def bindings_from_config(template, actual):
    result={}
    def visit(t,a):
        if isinstance(t,dict):
            if set(t)=={'$binding'}:result[t['$binding']]=a
            else:
                for key,value in t.items():visit(value,a[key])
        elif isinstance(t,list):
            if len(t)!=len(a):raise ValueError('Reference list differs from product template')
            for x,y in zip(t,a):visit(x,y)
    visit(template,actual)
    return result


def unit_arg(value):
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'


def prepare(a):
    root=a.output.resolve();reference=a.reference.resolve() if a.reference else None
    private=reference or a.private.resolve()
    if root.exists() or root==reference:raise ValueError('Use a new candidate runtime directory')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',a.project) or (reference and a.project==yaml.safe_load((reference/'compose.yaml').read_text())['name']):
        raise ValueError('Candidate project must be distinct')
    checks=read_json(a.checks);wake=read_json(a.wake_build/'receipt.json')
    if set(checks['roles'])!={'cli','gateway','services','mail'} or wake.get('smoke_returncode')!=0:
        raise ValueError('Candidate build checks missing')
    images={k:v['image'] for k,v in checks['roles'].items()};images['aisp-wake']=wake['image'];images['quiet-fan']=wake['image']
    images['ovms']=read_json(ROOT/'locks/accepted-images.json')['images']['ovms']
    for image in set(images.values()):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',image):raise ValueError('Immutable image required')
        subprocess.run(['docker','image','inspect',image],stdout=subprocess.DEVNULL,check=True)
    configs={}
    for profile in ['state','gateway-state']:
        if reference:
            actual=yaml.safe_load((reference/profile/'config.yaml').read_text())
            template=read_json(ROOT/'profiles'/f'{profile}.template.json')
            generated=render(profile,bindings_from_config(template,actual))
            if generated!=actual:raise ValueError('Reference behavior differs from source profile; review first')
        else:
            generated=render(profile,read_json(private/f'{profile}.bindings.json'))
        configs[profile]=generated
    # Resolve hardware by identity at preparation and again at activation.
    groups={p.name:p.stat().st_gid for p in Path('/dev/dri').glob('*')}
    render_group=next((v for k,v in groups.items() if k.startswith('renderD')),None)
    video_group=next((v for k,v in groups.items() if k.startswith('card')),None)
    if render_group is None or video_group is None:raise ValueError('GPU device groups unavailable')
    host={'PROJECT':a.project,'RUNTIME':str(root),'MODELS':str(a.models.resolve()),'AUDIO_RUNTIME':f'/run/user/{os.getuid()}',
          'UID':str(os.getuid()),'GID':str(os.getgid()),'VIDEO_GID':str(video_group),'RENDER_GID':str(render_group),**discover_fan()}
    composition=render_compose(host)
    for role,service in composition['services'].items():
        service['image']=images.get(role,images['services'])
        service['restart']='no'  # Candidate must not auto-start beside production after reboot.
    for model in ['llm','question-asr','wake-asr','tts']:
        if not (a.models/model).is_dir():raise ValueError('Required model directory missing')
    root.mkdir(parents=True,mode=0o700)
    os.chmod(root,0o700)
    for path in ['ipc/wake','ipc/asr','ipc/tts','ipc/media','ipc/recording-quiet','work','config/wake','units','host','deployment']:
        (root/path).mkdir(parents=True,exist_ok=True)
    # Root fan helper drops all capabilities and accesses this directory via its
    # host GID; owner-only permissions prevent even socket cleanup.
    (root/'ipc/recording-quiet').chmod(0o2770)
    for profile in configs:
        state=root/profile;state.mkdir()
        write_new(state/'config.yaml',configs[profile])
        shutil.copy2(ROOT/f'{profile}-SOUL.md',state/'SOUL.md')
        shutil.copytree(ROOT/profile/'skills',state/'skills')
        state.joinpath('plugins').symlink_to('/opt/hermes/plugins');state.joinpath('hermes-agent').symlink_to('/opt/hermes/app')
        for folder in ['run','logs','cache/box-voice','mail-queue','demo-home','sessions','cron']:(state/folder).mkdir(parents=True,exist_ok=True)
        # Explicit private-input allowlist: no auth caches, pairing requests,
        # pending messages, sessions, databases or queues are migrated.
        env=private/profile/'.env'
        if env.exists():
            if reference and str(reference) in env.read_text():raise ValueError('Private environment contains old runtime paths')
            shutil.copyfile(env,state/'.env');os.chmod(state/'.env',0o600)
    shutil.copy2(ROOT/'wake/config.yaml',root/'config/wake/config.yaml')
    for name in ['control.py','aisp_startup.py']:shutil.copy2(ROOT/'host'/name,root/'deployment'/name)
    for name in ['media-bridge.py','host-play-media.py']:shutil.copy2(ROOT/'host'/name,root/'host'/name)
    shutil.copytree(ROOT/'host/recording-quiet',root/'host/recording-quiet',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(ROOT/'build/candidate_ctl.py',root/'candidate_ctl.py')
    write_new(root/'compose.yaml',composition);(root/'compose.env').write_text('')
    write_new(root/'host-bindings.json',host)
    write_new(root/'control.json',{'compose_file':str(root/'compose.yaml'),'env_file':str(root/'compose.env'),'voice_container':a.project+'-voice','goodbye':str(a.resources.resolve()/'lifecycle-audio/goodbye.wav'),'production':False,'overlays':[],'aisp_startup_check':True})
    # User units are generated here but not linked/enabled/started.
    commands={
      'media':['/usr/bin/python3',root/'host/media-bridge.py','--socket',root/'ipc/media/service.sock','--player',root/'host/host-play-media.py'],
      'quiet':['/usr/bin/python3',root/'host/recording-quiet/server.py','--role','user','--uid',str(os.getuid()),'--socket',root/'ipc/recording-quiet/service.sock','--fan-socket',root/'ipc/recording-quiet/fan.sock','--state',root/'ipc/recording-quiet/volume-state.json','--config',root/'config/wake/config.yaml']}
    units=[]
    for role,command in commands.items():
        name=a.project+'-'+role+'.service';units.append(name)
        (root/'units'/name).write_text('[Unit]\nDescription=Hermes isolated candidate '+role+'\nAfter=pipewire.service\n[Service]\nType=simple\nExecStart='+' '.join(unit_arg(x) for x in command)+'\nRestart=on-failure\nRestartSec=1\nTimeoutStopSec=20\nUMask=0077\n')
    ref_files=([reference/'compose.yaml',reference/'control.json']+[reference/p/n for p in ['state','gateway-state'] for n in ['config.yaml','SOUL.md','.env'] if (reference/p/n).exists()]) if reference else []
    reference_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in ref_files}
    write_new(root/'candidate.json',{'project':a.project,'reference':str(reference) if reference else None,'units':units,'images':images,'status':'prepared-not-active','reference_hashes':reference_hashes,'source_commit':subprocess.check_output(['git','-C',str(ROOT.parent),'rev-parse','HEAD'],text=True).strip()})
    subprocess.run(['docker','compose','-f',str(root/'compose.yaml'),'config','--quiet'],check=True)
    subprocess.run(['systemd-analyze','--user','verify',*[str(root/'units'/x) for x in units]],check=True)
    print('Candidate runtime prepared; no services started:',root)


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--reference',type=Path)
    source.add_argument('--private',type=Path,help='Fresh install: profile bindings and optional per-profile .env files')
    for name in ['output','models','resources','checks','wake-build']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--project',required=True);prepare(p.parse_args())

if __name__=='__main__':main()
