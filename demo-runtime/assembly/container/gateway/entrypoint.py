#!/usr/bin/env python3
import json,os,sys,subprocess
from pathlib import Path
R=Path('/opt/hermes');S=Path(os.environ.get('HERMES_HOME','/state'))
def launch(args):os.execvpe(str(args[0]),list(map(str,args)),os.environ)
role=sys.argv[1] if len(sys.argv)>1 else 'check'
os.umask(0o077)
if role=='asr':
    os.environ.update(OCL_ICD_VENDORS=str(R/'gpu-runtime/vendors'),LD_LIBRARY_PATH=str(R/'gpu-runtime/driver'))
    launch([R/'asr-env/bin/python',R/'plugins/local-igpu-asr/worker.py','--model','/models/asr','--socket','/ipc/asr/asr.sock'])
if role=='tts':launch([R/'tts-env/bin/python',R/'app/scripts/local-ovms/kokoro_server.py','--model-dir','/models/tts','--socket','/ipc/tts/tts.sock','--threads','8'])
if role=='init':
    import yaml
    S.mkdir(parents=True,exist_ok=True)
    for name in ('plugins','hermes-agent'):
        dest=S/name
        if not dest.exists():dest.symlink_to(R/('app' if name=='hermes-agent' else name))
    # Native bundled sync writes its manifest; skills are private to this state.
    import shutil
    if not (S/'skills').exists():shutil.copytree(R/'skills',S/'skills')
    if not (S/'SOUL.md').exists():shutil.copy2(R/'SOUL.md',S/'SOUL.md')
    if not (S/'config.yaml').exists():
        c=json.loads((R/'behavior.json').read_text())
        c['stt']['openvino_igpu']['socket']='/ipc/asr/asr.sock'
        c['wake_word']['sherpa']['model_dir']=str(R/'resources/wakewords/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01')
        c['voice']['startup_cue_file']=str(R/'resources/box-voice/standby.wav')
        if c['voice']['end_phrase'].get('hint_file'):
            c['voice']['end_phrase']['hint_file']=str(R/'resources/voice-end-phrase/thats-all-hint.wav')
        c['tts']['providers']['kokoro-zh']['command']='/opt/hermes/core-env/bin/python /opt/hermes/app/scripts/local-ovms/kokoro_client.py --socket /ipc/tts/tts.sock --input {input_path} --output {output_path} --english-voice af_maple --speed {speed}'
        c['platforms']={'email':{'home_channel':{'platform':'email','name':'Default email recipients','chat_id':c['voice_delivery']['default_recipient']}}}
        (S/'config.yaml').write_text(yaml.safe_dump(c,sort_keys=False))
    for sub in ('run','logs','mail-queue','demo-home','cache/box-voice'):(S/sub).mkdir(parents=True,exist_ok=True)
    shutil.copy2(R/'resources/box-voice/goodbye.wav',S/'cache/box-voice/goodbye.wav')
    print('Initialized isolated state',S);raise SystemExit(0)
if role=='check':
    import cli,model_tools,sherpa_onnx
    print(json.dumps({'python':sys.version,'core':str(R/'app'),'uid':os.getuid(),'imports':'OK'}));raise SystemExit(0)
if role=='gateway':launch([R/'core-env/bin/python','-m','hermes_cli.main','gateway','run'])
if role=='mail':launch([R/'core-env/bin/python',R/'plugins/email-queue/mail_queue.py','serve','--home',S])
if role=='home':launch([R/'core-env/bin/python',R/'plugins/demo-home/simulator.py','serve','--db',S/'demo-home/state.sqlite3'])
if role=='scene':launch([R/'core-env/bin/python','-m','hermes_cli.voice_scenes',*sys.argv[2:]])
if role=='cli':
    os.environ.update(HERMES_CLI_VOICE_AUTO_START='1',HERMES_CLI_WAKE_READY_CUE='1',HERMES_CLI_PTT_ONESHOT='1',TERM='xterm-256color')
    launch([R/'core-env/bin/python','-m','hermes_cli.main','--cli',*sys.argv[2:]])
if role=='python':launch([R/'core-env/bin/python',*sys.argv[2:]])
raise SystemExit('Unknown service role: '+role)
