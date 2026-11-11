#!/usr/bin/env python3
"""Host command facade: Docker owns CLI children; scene RPC stays native."""
import json,os,subprocess,sys
from pathlib import Path
config=Path(os.environ.get('HERMES_CONTAINER_CONTROL',str(Path.home()/'.config/hermes-container/active.json')))
c=json.loads(config.read_text());name=c['voice_container']
compose=['docker','compose','--env-file',c['env_file'],'-f',c['compose_file']]
for overlay in c.get('overlays',[]):compose+=['-f',overlay]
def running():
    p=subprocess.run(['docker','inspect','--format','{{.State.Running}}',name],capture_output=True,text=True)
    if p.returncode and not ('no such container' in p.stderr.lower() or 'no such object' in p.stderr.lower()):
        raise SystemExit('Cannot inspect voice container; refusing startup.')
    return p.returncode==0 and p.stdout.strip()=='true'
def remove_exited_voice():
    """Remove only this project's exited one-off CLI, never volumes or running work."""
    p=subprocess.run(['docker','container','inspect',name],capture_output=True,text=True)
    if p.returncode:
        if 'no such container' in p.stderr.lower() or 'no such object' in p.stderr.lower():
            return
        raise SystemExit('Cannot inspect voice container; refusing cleanup.')
    item=json.loads(p.stdout)[0]
    if item['State']['Status'] not in ('exited','dead'):
        raise SystemExit('Voice container is not exited; refusing cleanup. Retry status/start.')
    project=subprocess.run(compose+['config','--format','json'],capture_output=True,text=True,check=True)
    labels=item['Config'].get('Labels') or {}
    if (labels.get('com.docker.compose.project') != json.loads(project.stdout)['name'] or
        labels.get('com.docker.compose.service') != 'cli' or
        labels.get('com.docker.compose.oneoff','').lower() != 'true'):
        raise SystemExit('Voice container ownership mismatch; refusing cleanup.')
    # Use the inspected ID, not a reusable name. No --force or --volumes.
    result=subprocess.run(['docker','container','rm',item['Id']],capture_output=True,text=True)
    if result.returncode and not ('no such container' in result.stderr.lower() or 'no such object' in result.stderr.lower()):
        raise SystemExit('Voice container could not be removed safely; retry status/start.')
def scene(args):return subprocess.run(['docker','exec',name,'/opt/hermes/core-env/bin/python','-m','hermes_cli.voice_scenes',*args]).returncode
kind=sys.argv[1];args=sys.argv[2:]
if kind=='scene':
    if not running():raise SystemExit('Voice CLI is not running; use hermes-box-voice start')
    raise SystemExit(scene(args))
if kind=='hermes':
    cmd=compose+['run','--rm']+([] if sys.stdin.isatty() else ['-T'])+['cli','python','-m','hermes_cli.main',*args]
    os.execvp(cmd[0],cmd)
if kind=='mode':
    if args==['voice','--run']:kind='voice';args=['start']
    elif args==['keyboard','--run']:
        cmd=compose+['run','--rm','cli','python','-m','hermes_cli.main','--cli'];os.execvp(cmd[0],cmd)
    else:raise SystemExit('Container mode supports voice --run or keyboard --run; configure via hermes config.')
if kind!='voice' or not args or args[0] not in ('start','stop','toggle','status'):raise SystemExit('Expected voice start/stop/toggle/status')
action=args[0]
if action=='toggle':action='stop' if running() else 'start'
if action=='status':
    if running():raise SystemExit(scene(['status']))
    print(json.dumps({'state':'stopped','runtime':'container'}));raise SystemExit(0)
if action=='stop':
    if running():
        ready=subprocess.run(['docker','exec',name,'/opt/hermes/core-env/bin/python','-m','hermes_cli.voice_scenes','status'],capture_output=True).returncode==0
        subprocess.run(['docker','stop','--timeout','35',name],check=True)
        if ready:subprocess.run(['pw-play',c['goodbye']],check=False,timeout=15)
    remove_exited_voice()
    raise SystemExit(0)
if running():raise SystemExit(scene(['status']))
remove_exited_voice()
if c.get('production'):
    subprocess.run(compose+['up','-d','asr','tts','mail','home','gateway'],check=True)
if c.get('aisp_startup_check', False):
    from aisp_startup import await_eos
    await_eos(compose, name)
cmd=compose+['run','--rm','--name',name]
if not(sys.stdin.isatty() and sys.stdout.isatty()):cmd+=['-d']
cmd+=['cli','cli'];os.execvp(cmd[0],cmd)
