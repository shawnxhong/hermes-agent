#!/usr/bin/env python3
"""Fresh-host materializer and systemd-owned lifecycle; never copies runtime DBs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.request
import yaml


def run(cmd, **kwargs):
    return subprocess.run(cmd,check=True,timeout=300,**kwargs)


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()


def restore(value,bindings):
    if isinstance(value,dict):
        if set(value)=={'$binding'}:return bindings[value['$binding']]
        return {k:restore(v,bindings) for k,v in value.items()}
    if isinstance(value,list):return [restore(v,bindings) for v in value]
    return value


def substitute(value,bindings):
    if isinstance(value,str):
        for k,v in bindings.items():value=value.replace('${'+k+'}',str(v))
        if '${' in value:raise ValueError('Unresolved host binding')
        return value
    if isinstance(value,list):return [substitute(v,bindings) for v in value]
    if isinstance(value,dict):return {k:substitute(v,bindings) for k,v in value.items()}
    return value


def compose(root):return ['docker','compose','-f',str(root/'compose.yaml')]


def check_kit(kit):
    for rel,digest in json.loads((kit/'SHA256.json').read_text()).items():
        path=(kit/rel).resolve()
        if not path.is_relative_to(kit.resolve()) or sha(path)!=digest:raise ValueError('Kit hash mismatch: '+rel)


def brave_only_config(cfg):
    """Explicit deployment policy: no mail tools, recipients or IM transports."""
    for tools in cfg.get('platform_toolsets',{}).values():
        if 'email_queue' in tools:tools.remove('email_queue')
    agent=cfg.setdefault('agent',{})
    disabled=agent.setdefault('disabled_toolsets',[])
    for name in ('email_queue','voice_delivery'):
        if name not in disabled:disabled.append(name)
    prompt=agent.get('system_prompt','')
    agent['system_prompt']=prompt+' Email delivery is unavailable on this device. Do not promise to send email.'
    plugins=cfg.get('plugins',{}).get('enabled',[])
    if 'email-queue' in plugins:plugins.remove('email-queue')
    skills=cfg.setdefault('skills',{}).setdefault('disabled',[])
    if 'email-results' not in skills:skills.append('email-results')
    platforms=cfg.setdefault('platforms',{})
    for name in set(platforms)|{'email','feishu','weixin','wechat'}:
        platforms.setdefault(name,{})['enabled']=False
    platforms['email'].pop('home_channel',None)
    for section in ('travel_voice','voice_delivery'):
        cfg.get(section,{}).pop('default_recipient',None)
    return cfg


def materialize(a):
    check_kit(a.kit)
    if not re.fullmatch('[a-z][a-z0-9_-]{0,30}',a.host):raise ValueError('Use stable lowercase host id')
    for p in (a.root,a.assets,a.audio_runtime,a.kit,a.bindings):
        if not p.is_absolute() or any(c in str(p) for c in '\n\r %'):raise ValueError('Use absolute paths without spaces or systemd specifiers')
    for category,rel in {'llm':'models/llm','question-asr':'models/question-asr','wake-asr':'models/wake-asr','tts':'models/tts','prompt-audio':'resources/prompt-audio','lifecycle-audio':'resources/lifecycle-audio'}.items():
        manifest=json.loads((a.kit/'asset-manifests'/(category+'.json')).read_text())
        for name,entry in manifest.items():
            path=a.assets/rel/name
            if not path.is_file() or sha(path)!=entry['sha256']:raise ValueError('Asset mismatch '+str(path))
    a.root.mkdir(parents=True,exist_ok=False,mode=0o700)
    shutil.copytree(a.kit,a.root/'kit')
    for profile in ('state','gateway-state'):
        dest=a.root/profile
        shutil.copytree(a.kit/'defaults'/profile,dest)
        template=json.loads((a.kit/'profiles'/(profile+'.template.json')).read_text())
        bindings=json.loads((a.bindings/(profile+'.bindings.json')).read_text())
        cfg=restore(template,bindings)
        if a.brave_only:cfg=brave_only_config(cfg)
        if cfg.get('platforms',{}).get('weixin',{}).get('enabled'):
            raise ValueError('Fresh host must not implicitly become Weixin owner')
        (dest/'config.yaml').write_text(yaml.safe_dump(cfg,sort_keys=False))
        # Only explicitly provisioned credential files; no databases or queues.
        env=a.bindings/(profile+'.env')
        if not env.is_file():raise ValueError('Missing private environment file: '+str(env))
        shutil.copy2(env,dest/'.env');(dest/'.env').chmod(0o600)
    for rel in ('ipc/wake','ipc/asr','ipc/tts','ipc/media','state/mail-queue','work'):
        (a.root/rel).mkdir(parents=True,exist_ok=True)
    values={'RUNTIME':a.root,'ASSETS':a.assets,'AUDIO_RUNTIME':a.audio_runtime,
            'PROJECT':'hermes-'+a.host,'USER_IDS':f'{os.getuid()}:{os.getgid()}',
            'VIDEO_GID':a.video_gid,'RENDER_GID':a.render_gid}
    doc=substitute(yaml.safe_load((a.kit/'compose.template.yaml').read_text()),values)
    if a.brave_only:
        for role in ('gateway','mail'):doc['services'].pop(role,None)
    for s in doc['services'].values():
        for m in s.get('volumes',[]):
            if m['target']=='/config/aliases.json':m['source']=str(a.root/'kit/wake-aliases.json')
    (a.root/'compose.yaml').write_text(yaml.safe_dump(doc,sort_keys=False))
    (a.root/'compose.env').touch()
    shutil.copy2(Path(__file__).resolve(),a.root/'lifecycle.py')
    (a.root/'deployment-policy.json').write_text(json.dumps({'brave_only':a.brave_only,'mail_enabled':not a.brave_only,'gateway_enabled':not a.brave_only},indent=2))
    (a.root/'control.json').write_text(json.dumps({'compose_file':str(a.root/'compose.yaml'),
        'env_file':str(a.root/'compose.env'),'voice_container':'hermes-'+a.host+'-voice',
        'goodbye':str(a.assets/'resources/lifecycle-audio/goodbye.wav'),'production':False,'overlays':[]},indent=2))
    run(compose(a.root)+['config','--quiet'])
    print('Prepared, NOT started:',a.root)


def pending(root):
    path=root/'state/mail-queue/queue.sqlite'
    if not path.exists():return 0
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
        return db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','sending')").fetchone()[0]


def stop(root):
    c=json.loads((root/'control.json').read_text())
    # Includes compose-run one-off voice containers, not just service containers.
    project=yaml.safe_load((root/'compose.yaml').read_text())['name']
    ids=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project='+project,
                                 '--filter','label=com.docker.compose.service=cli'],text=True).split()
    if ids:run(['docker','stop','--timeout','35',*ids])
    if 'gateway' in yaml.safe_load((root/'compose.yaml').read_text())['services']:
        run(compose(root)+['stop','--timeout','45','gateway'])
    deadline=time.monotonic()+120
    while pending(root) and time.monotonic()<deadline:time.sleep(2)
    if pending(root):raise ValueError('Pending mail: leave worker running; do not start another stack')
    run(compose(root)+['stop','--timeout','35'])


def start(root):
    doc=yaml.safe_load((root/'compose.yaml').read_text())
    active=[json.loads(line) for line in subprocess.check_output(['docker','ps','--format','json'],text=True).splitlines()]
    for c in active:
        if ('hermes' in c['Names'] or 'ovms' in c['Names']) and c.get('Labels','').find('com.docker.compose.project='+doc['name']+',')<0:
            # Label membership is validated using inspect, not name similarity alone.
            labels=json.loads(subprocess.check_output(['docker','inspect',c['ID']],text=True))[0]['Config'].get('Labels') or {}
            if labels.get('com.docker.compose.project')!=doc['name']:raise ValueError('Another Hermes/OVMS stack is active')
    for s in doc['services'].values():
        run(['docker','image','inspect',s['image']],stdout=subprocess.DEVNULL)
        for m in s.get('volumes',[]):
            if not Path(m['source']).exists():raise ValueError('Missing bind source '+m['source'])
    for p in ('cli','gateway'):
        if p in doc['services']:run(compose(root)+['run','--rm','--no-deps','-T',p,'init'])
    roles=[r for r in ('ovms','asr','tts','home','mail','aisp-wake','gateway') if r in doc['services']]
    run(compose(root)+['up','-d',*roles])
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/v3/models',timeout=3) as r:
                if not json.load(r).get('data'):raise ValueError('Model loading')
            for rel in ('ipc/asr/asr.sock','ipc/tts/tts.sock','ipc/wake/service.sock','state/mail-queue/service.sock'):
                if rel.startswith('state/mail') and 'mail' not in doc['services']:continue
                with socket.socket(socket.AF_UNIX) as s:s.settimeout(2);s.connect(str(root/rel))
            return
        except (OSError,ValueError):time.sleep(2)
    raise TimeoutError('Readiness timeout; inspect containers, stop before rollback')


def install_units(root, replace=False):
    # Fresh installation only; existing unit files are never overwritten.
    units=Path.home()/'.config/systemd/user';units.mkdir(parents=True,exist_ok=True)
    targets=[units/'hermes-production.service',units/'hermes-production-media.service']
    if any(p.exists() or p.is_symlink() for p in targets) and not replace:raise FileExistsError('Existing production units; preserve and review before upgrade')
    script=root/'lifecycle.py'
    targets[0].write_text(f'[Unit]\nDescription=Hermes pinned production stack\nRequires=hermes-production-media.service\nAfter=hermes-production-media.service\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/python3 {script} start --root {root}\nExecStop=/usr/bin/python3 {script} stop --root {root}\nTimeoutStartSec=600\nTimeoutStopSec=300\n\n[Install]\nWantedBy=default.target\n')
    targets[1].write_text(f'[Unit]\nDescription=Hermes media bridge\n\n[Service]\nExecStart=/usr/bin/python3 {root}/kit/host/media-bridge.py --socket {root}/ipc/media/service.sock --player {root}/kit/host/host-play-media.py\nRestart=on-failure\nUMask=0077\n\n[Install]\nWantedBy=default.target\n')
    run(['systemctl','--user','daemon-reload'])
    run(['systemctl','--user','enable','hermes-production.service'])
    (root/'installed-units.json').write_text(json.dumps({str(p):sha(p) for p in targets},indent=2))
    print('Units installed/enabled, NOT started. Audio login/PipeWire readiness and lingering require host validation.')


def rollback(root, previous):
    if not previous or previous.resolve()==root.resolve():raise ValueError('Distinct prepared previous root required')
    if not (previous/'compose.yaml').is_file() or not (previous/'kit/install.py').is_file():raise ValueError('Previous deployment missing')
    installed=json.loads((root/'installed-units.json').read_text())
    for name,digest in installed.items():
        if sha(Path(name))!=digest:raise ValueError('Unit drift; refuse rollback')
    # Stop producers and drain writes BEFORE stopping media or rebinding boot units.
    stop(root)
    run(['systemctl','--user','stop','hermes-production.service','hermes-production-media.service'])
    install_units(previous,replace=True)
    run(['systemctl','--user','start','hermes-production.service'])
    print('Previous stack and boot bindings restored. No data or queues copied.')


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','start','stop','install-units','voice','scene','status','rollback'])
    p.add_argument('--root',type=Path,required=True)
    for n in ('kit','assets','bindings','audio-runtime','previous'):p.add_argument('--'+n,type=Path)
    p.add_argument('--host');p.add_argument('--video-gid',type=int);p.add_argument('--render-gid',type=int)
    p.add_argument('--brave-only',action='store_true')
    p.add_argument('extra',nargs='*')
    a=p.parse_intermixed_args()
    if a.action=='prepare':
        if any(getattr(a,k) is None for k in ('kit','assets','bindings','audio_runtime','host','video_gid','render_gid')):p.error('prepare requires kit/assets/bindings/audio-runtime/host/video-gid/render-gid')
        materialize(a)
    elif a.action=='start':start(a.root)
    elif a.action=='stop':stop(a.root)
    elif a.action=='install-units':install_units(a.root)
    elif a.action=='rollback':
        rollback(a.root,a.previous)
    elif a.action=='status':run(compose(a.root)+['ps','-a'])
    else:
        if a.action=='voice':
            # Voice must not silently start an uninitialized/offline stack.
            ids=subprocess.check_output(compose(a.root)+['ps','--status','running','-q','aisp-wake'],text=True).strip()
            if not ids:raise ValueError('Start production services before voice')
        env=dict(os.environ,HERMES_CONTAINER_CONTROL=str(a.root/'control.json'))
        os.execve('/usr/bin/python3',['/usr/bin/python3',str(a.root/'kit/host/control.py'),a.action,*(['start'] if a.action=='voice' else a.extra)],env)


if __name__=='__main__':main()
