#!/usr/bin/env python3
"""Explicit candidate switch with isolated state and original-container rollback."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parent
PRODUCTION_UNITS=['hermes-production.service','hermes-container-media.service','hermes-recording-quiet.service']


def run(args, **kwargs):
    return subprocess.run(list(map(str,args)),check=True,timeout=kwargs.pop('timeout',180),**kwargs)


def output(args):return subprocess.check_output(list(map(str,args)),text=True,timeout=30).strip()

def load(path):return json.loads(path.read_text())

def save(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.chmod(0o600);tmp.replace(path)


def compose():return ['docker','compose','-f',str(ROOT/'compose.yaml')]


def inspect(ids):return json.loads(output(['docker','inspect',*ids])) if ids else []


def project_containers(project,running=False):
    args=['docker','ps']+([] if running else ['-a'])+['-q','--filter','label=com.docker.compose.project='+project]
    return inspect(output(args).split())


def identity(item):
    return {'id':item['Id'],'image':item['Image'],'mounts':sorted((m['Type'],m.get('Source',''),m['Destination'],bool(m.get('RW'))) for m in item.get('Mounts',[])),
            'project':(item['Config'].get('Labels') or {}).get('com.docker.compose.project'),
            'service':(item['Config'].get('Labels') or {}).get('com.docker.compose.service')}


def pending(root):
    path=root/'state/mail-queue/queue.sqlite'
    if not path.exists():return 0
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
        return db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','sending')").fetchone()[0]


def drain(root):
    deadline=time.monotonic()+120
    while pending(root) and time.monotonic()<deadline:time.sleep(1)
    if pending(root):raise RuntimeError('Mail still pending; worker retained. Resolve delivery before rollback.')


def reference_ok(config):
    for name,expected in config['reference_hashes'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Production configuration changed; review before switching/restoring')


def refresh_fan():
    # Identity-based discovery avoids stale hwmon indices after reboot.
    fans=[p.resolve() for p in Path('/sys/class/thermal').glob('cooling_device*') if (p/'type').is_file() and (p/'type').read_text().strip()=='TFN1']
    rpms=[(p/'fan1_input').resolve() for p in Path('/sys/class/hwmon').glob('*') if (p/'name').is_file() and (p/'name').read_text().strip()=='acpi_fan' and (p/'fan1_input').is_file()]
    if len(fans)!=1 or len(rpms)!=1:raise RuntimeError('Fan hardware ambiguous; no services changed')
    doc=load(ROOT/'compose.yaml');mapping={'/fan/state':fans[0]/'cur_state','/fan/maximum':fans[0]/'max_state','/fan/type':fans[0]/'type','/fan/rpm':rpms[0]}
    for volume in doc['services']['quiet-fan']['volumes']:
        if volume['target'] in mapping:volume['source']=str(mapping[volume['target']])
    save(ROOT/'compose.yaml',doc)


def preflight(config):
    reference_ok(config);refresh_fan();doc=load(ROOT/'compose.yaml')
    if doc['name']!=config['project']:raise RuntimeError('Candidate project changed')
    for name,service in doc['services'].items():
        expected=config['images'].get(name,config['images']['services'])
        if service['image']!=expected:raise RuntimeError('Candidate image selector changed')
        if service.get('restart')!='no':raise RuntimeError('Candidate auto-restart must remain disabled')
        for volume in service.get('volumes',[]):
            if not Path(volume['source']).exists():raise RuntimeError('Missing bind source: '+volume['source'])
    for unit in config['units']:
        installed=Path.home()/'.config/systemd/user'/unit
        if (installed.exists() or installed.is_symlink()) and installed.resolve()!=(ROOT/'units'/unit).resolve():
            raise RuntimeError('Existing candidate unit belongs to another runtime')
    run(compose()+['config','--quiet'])
    print('Candidate preflight passed; no services changed.',flush=True)


BACKGROUND_SERVICES={'ovms','asr','tts','home','mail','gateway','quiet-fan','aisp-wake'}


def services_ready(containers):
    states={identity(item)['service']:item['State'] for item in containers}
    for name in BACKGROUND_SERVICES:
        state=states.get(name,{})
        if state.get('Status') in ('exited','dead'):
            raise RuntimeError('Required service exited: '+name)
        if not state.get('Running') or state.get('Health',{}).get('Status','healthy')!='healthy':
            return False
    return True


def stop_candidate_writers(config):
    writers=[x['Id'] for x in project_containers(config['project'],True) if identity(x)['service'] in ('cli','gateway')]
    if writers:run(['docker','stop','--time','45',*writers])


def process_token(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except FileNotFoundError:return None


def restore(config,journal):
    reference_ok(config)
    if journal['phase']=='restored':return
    marker=ROOT/'voice-launch.json'
    if marker.exists():
        owner=load(marker)
        if process_token(owner['pid'])==owner['start']:
            raise RuntimeError('Close the candidate voice terminal with Ctrl+C before rollback')
    stop_candidate_writers(config);drain(ROOT)
    run(compose()+['stop','--timeout','35'])
    for unit in config['units']:
        state=subprocess.run(['systemctl','--user','show',unit,'-p','LoadState','--value'],capture_output=True,text=True,timeout=10)
        if state.stdout.strip() not in ('','not-found'):run(['systemctl','--user','stop',unit],timeout=45)
    run(['/usr/bin/python3',ROOT/'host/host-play-media.py','stop'])
    # Check all original container identities/mounts before restarting any.
    current={x['Id']:identity(x) for x in inspect([x['id'] for x in journal['containers']])}
    for old in journal['containers']:
        if json.loads(json.dumps(current.get(old['id'])))!=old:raise RuntimeError('Original container drift; refusing guessed recovery')
    for unit in journal['active_units']:run(['systemctl','--user','start',unit],timeout=650)
    ids=[x['id'] for x in journal['containers']]
    if ids:run(['docker','start',*ids],timeout=300)
    journal['phase']='restored';save(ROOT/'switch.json',journal)
    print('Original running containers and previously active units restored. Production entrypoint was never changed.')


def activate(config):
    preflight(config)
    journal_path=ROOT/'switch.json'
    if journal_path.exists() and load(journal_path)['phase']!='restored':raise RuntimeError('Switch already recorded; use voice/status/rollback')
    if project_containers(config['project'],True):raise RuntimeError('Candidate already running')
    ref=Path(config['reference']) if config.get('reference') else None
    ref_project=None;containers=[];units=[]
    if ref:
        ref_project=json.loads(output(['docker','compose','-f',str(ref/'compose.yaml'),'config','--format','json']))['name']
        containers=project_containers(ref_project,True)
        if any(identity(x)['service']=='cli' for x in containers):raise RuntimeError('Close the production voice CLI before activating the candidate')
        units=[u for u in PRODUCTION_UNITS if subprocess.run(['systemctl','--user','is-active','--quiet',u]).returncode==0]
    if (ref and pending(ref)) or pending(ROOT):raise RuntimeError('Pending mail found; allow delivery to finish before switching')
    # Reject occupied ports before stopping anything on a fresh host.
    if not ref:
        for port in (8000,8769):
            with socket.socket() as probe:probe.bind(('127.0.0.1',port))
    journal={'phase':'suspending','containers':[identity(x) for x in containers],'active_units':units}
    save(journal_path,journal)  # Persist recovery information before stopping anything.
    writers=[x['Id'] for x in containers if identity(x)['service']=='gateway']
    if writers:run(['docker','stop','--time','45',*writers])
    if ref:drain(ref)
    for unit in units:run(['systemctl','--user','stop',unit],timeout=650)
    if containers:run(['docker','stop','--time','35',*[x['Id'] for x in containers]])
    if ref_project and project_containers(ref_project,True):raise RuntimeError('Production still running; candidate not started')
    run(['/usr/bin/python3',ROOT/'host/host-play-media.py','stop'])
    for port in (8000,8769):
        with socket.socket() as probe:probe.bind(('127.0.0.1',port))
    journal['phase']='starting';save(journal_path,journal)
    for unit in config['units']:
        installed=Path.home()/'.config/systemd/user'/unit
        if (installed.exists() or installed.is_symlink()) and installed.resolve()!=(ROOT/'units'/unit).resolve():
            raise RuntimeError('Existing candidate unit belongs to another path')
    run(['systemctl','--user','link',*[ROOT/'units'/u for u in config['units']]])
    run(['systemctl','--user','daemon-reload'])
    run(['systemctl','--user','start',*config['units']])
    run(compose()+['up','-d','ovms','asr','tts','home','mail','gateway','quiet-fan','aisp-wake'],timeout=360)
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        sockets=all((ROOT/p).is_socket() for p in ['ipc/asr/asr.sock','ipc/tts/tts.sock','ipc/wake/service.sock','ipc/media/service.sock','ipc/recording-quiet/service.sock'])
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/v3/models',timeout=2) as response:ready=bool(json.load(response).get('data'))
        except (OSError,ValueError):ready=False
        alive=services_ready(project_containers(config['project']))
        if sockets and ready and alive:break
        time.sleep(1)
    else:raise RuntimeError('Candidate readiness timed out; run rollback')
    journal['phase']='active';save(journal_path,journal)
    print('Candidate backgrounds ready. Run: hermes-source-test voice')


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['check','status','activate','voice','rollback']);a=parser.parse_args();config=load(ROOT/'candidate.json')
    with (ROOT/'switch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='check':preflight(config)
        elif a.action=='status':
            print('Switch:',load(ROOT/'switch.json')['phase'] if (ROOT/'switch.json').exists() else 'not activated')
            run(compose()+['ps','-a'])
        elif a.action=='activate':activate(config)
        elif a.action=='rollback':
            if not (ROOT/'switch.json').exists():raise RuntimeError('No switch has occurred; nothing to roll back')
            restore(config,load(ROOT/'switch.json'))
        elif a.action=='voice':
            if not (ROOT/'switch.json').exists() or load(ROOT/'switch.json')['phase']!='active':raise RuntimeError('Activate the candidate first')
            save(ROOT/'voice-launch.json',{'pid':os.getpid(),'start':process_token(os.getpid())})
            os.environ['HERMES_CONTAINER_CONTROL']=str(ROOT/'control.json')
            os.execv('/usr/bin/python3',['/usr/bin/python3',str(ROOT/'deployment/control.py'),'voice','start'])


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,sqlite3.Error) as error:
        print('Candidate action stopped:',error,file=sys.stderr)
        print('If activation had begun, use hermes-source-test rollback; production selectors are unchanged.',file=sys.stderr)
        sys.exit(1)
