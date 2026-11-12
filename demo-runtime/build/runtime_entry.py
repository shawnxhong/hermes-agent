#!/usr/bin/env python3
"""Fresh-install command facade. Never switches a different installation."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent


def call(*args):return subprocess.run([sys.executable,*map(str,args)],check=True)


def main():
    args=sys.argv[1:]
    if args==['--help'] or not args:
        print('hermes-box-voice start|stop|toggle|status; hermes-box-scene switch SCENE; hermes-box-runtime check|status|shutdown');return
    kind,*rest=args
    if kind=='runtime':
        if rest not in [['check'],['status'],['shutdown']]:raise ValueError('Unknown runtime action')
        call(ROOT/'candidate_ctl.py','rollback' if rest==['shutdown'] else rest[0]);return
    if kind=='scene':
        os.environ['HERMES_CONTAINER_CONTROL']=str(ROOT/'control.json')
        os.execv(sys.executable,[sys.executable,str(ROOT/'deployment/control.py'),'scene',*rest])
    if kind!='voice' or rest not in [['start'],['stop'],['toggle'],['status']]:raise ValueError('Expected voice start/stop/toggle/status')
    if rest==['toggle']:
        config=json.loads((ROOT/'control.json').read_text())
        r=subprocess.run(['docker','inspect','--format','{{.State.Running}}',config['voice_container']],capture_output=True,text=True)
        rest=['stop' if r.returncode==0 and r.stdout.strip()=='true' else 'start']
    if rest==['start']:
        # Requiring a real user's session avoids accidentally mounting another user's audio.
        audio=Path('/run/user')/str(os.getuid())
        if not (audio/'pulse/native').is_socket():raise RuntimeError('Log in to this user’s desktop/PipeWire session before voice start')
        subprocess.run(['systemctl','--user','show-environment'],stdout=subprocess.DEVNULL,check=True)
        with (ROOT/'entry.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            config=json.loads((ROOT/'candidate.json').read_text())
            if config.get('reference'):raise RuntimeError('Fresh-install wrapper cannot switch a reference production runtime')
            journal=ROOT/'switch.json'
            state=json.loads(journal.read_text()) if journal.exists() else None
            if state and state['phase']=='active':
                running=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project='+config['project']],text=True).strip()
                if not running:
                    # After reboot nothing is active. Retain the old journal and let
                    # fresh activation restart its own queues/services only.
                    journal.replace(ROOT/'previous-switch.json');state=None
            if not state or state['phase']=='restored':call(ROOT/'candidate_ctl.py','activate')
            elif state['phase']!='active':raise RuntimeError('Previous activation incomplete; use hermes-box-runtime shutdown before retrying')
        os.execv(sys.executable,[sys.executable,str(ROOT/'candidate_ctl.py'),'voice'])
    os.environ['HERMES_CONTAINER_CONTROL']=str(ROOT/'control.json')
    os.execv(sys.executable,[sys.executable,str(ROOT/'deployment/control.py'),'voice',*rest])

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:raise SystemExit(str(exc))
