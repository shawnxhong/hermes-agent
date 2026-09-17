#!/usr/bin/env python3
"""Real OVMS/native harness, real queue IPC, simulated search, NO SMTP worker."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import yaml


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=['direct','travel','mixed'],default='direct')
    parser.add_argument('--platform',choices=['cli','feishu','weixin'],default='cli')
    parser.add_argument('--strict-single-call',action='store_true',help='Also fail on model duplicates even if the queue prevents duplicate mail')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    live=Path('/home/agentdemo/.hermes')
    home=Path(tempfile.mkdtemp(prefix='mail-e2e-'))
    config=yaml.safe_load((live/'config.yaml').read_text())
    for platform in {args.platform,'cli','feishu','weixin'}:
        config.setdefault('platform_toolsets',{})[platform]=['web','file','skills','cronjob','tts','email_queue','demo_home','local_media']
    config.setdefault('agent',{}).setdefault('disabled_toolsets',[]).append('voice_delivery')
    config.setdefault('plugins',{}).setdefault('enabled',[]).append('email-queue')
    config['memory']={'enabled':False}
    (home/'config.yaml').write_text(yaml.safe_dump(config));(home/'config.yaml').chmod(0o600)
    for name in ('skills','plugins'):
        shutil.copytree((live/name).resolve(),home/name,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(live/'SOUL.md',home/'SOUL.md')
    shutil.copytree(root/'scripts/local-ovms/plugins/email-queue',home/'plugins/email-queue',dirs_exist_ok=True)
    for rel in ('email/email-results/SKILL.md','productivity/travel-concierge/SKILL.md','productivity/flight-search/SKILL.md'):
        shutil.copy2(root/'skills'/rel,home/'skills'/rel)
    os.environ.update(HERMES_HOME=str(home),HERMES_INTERACTIVE='1' if args.platform=='cli' else '0',
                      HERMES_GATEWAY_SESSION='0' if args.platform=='cli' else '1',HERMES_SESSION_PLATFORM=args.platform,
                      NO_PROXY='localhost,127.0.0.1',no_proxy='localhost,127.0.0.1')
    sys.path.insert(0,str(root))
    spec=importlib.util.spec_from_file_location('_queue_test',root/'scripts/local-ovms/plugins/email-queue/mail_queue.py')
    service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
    store=service.Store(home/'mail-queue')
    server=service.Server(store.folder/'service.sock',store,lambda:['recipient@example.test'])
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    from hermes_cli.plugins import discover_plugins
    discover_plugins()
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions
    toolsets=sorted(_get_platform_tools(config,args.platform))
    names={x['function']['name'] for x in get_tool_definitions(enabled_toolsets=toolsets,quiet_mode=True)}
    assert 'email_send' in names and 'send_message' not in names and len(names)==15,names
    from run_agent import AIAgent
    import run_agent
    from hermes_state import SessionDB
    from hermes_cli.voice_response_policy import build_voice_turn_prefix
    from hermes_cli.native_voice import NativeSpeechDelivery
    original=run_agent.handle_function_call
    calls=[]
    def dispatch(name,arguments,*a,**kw):
        if name in {'skill_view','skills_list','email_send'}:
            result=original(name,arguments,*a,**kw)
        elif name in {'web_search','web_extract'}:
            result=json.dumps({'success':True,'text':'London highlights include the British Museum, Buckingham Palace and the South Bank. Check current opening hours before visiting.','source':'https://en.wikivoyage.org/wiki/London'})
        else:
            result=json.dumps({'error':'Other actions disabled in this isolated test'})
        calls.append({'tool':name,'result':result if name=='email_send' else 'fixture/read-only'})
        return result
    agent=AIAgent(model='qwen3.6-35b-a3b',provider='custom',base_url='http://127.0.0.1:8000/v3',
        api_key='local-ovms',api_mode='chat_completions',quiet_mode=True,max_iterations=8,max_tokens=1024,
        skip_memory=True,skip_context_files=True,load_soul_identity=True,
        ephemeral_system_prompt=config.get('agent',{}).get('system_prompt'),
        platform=args.platform,session_db=SessionDB(home/'state.db'),enabled_toolsets=toolsets)
    questions={'direct':['Please email the message "Test details for my trip" to my default recipients.'],
        'travel':['I want to travel to London. Please give me some advice.','Send those detailed information to my emails.'],
        'mixed':['What does RSVP mean?','Please email that explanation to my default recipients.','What is two plus two?']}[args.case]
    history=[];records=[]
    print('TEST_HOME',home,flush=True)
    try:
        with patch.object(run_agent,'handle_function_call',dispatch):
            for question in questions:
                start=time.monotonic(); count=len(calls)
                speech=NativeSpeechDelivery(queue.Queue(),threading.Event()) if args.platform=='cli' else None
                text=build_voice_turn_prefix()+question if speech else question
                result=agent.run_conversation(text,conversation_history=history,persist_user_message=question,
                    input_modality='voice' if speech else 'text',task_id=agent.session_id,stream_callback=speech)
                history=result.get('messages',history)
                records.append({'question':question,'seconds':round(time.monotonic()-start,2),
                                'response':result.get('final_response'),'calls':calls[count:],'jobs':store.status()})
                (home/'receipt.json').write_text(json.dumps(records,indent=2))
                print(json.dumps(records[-1]),flush=True)
        assert len(store.status())==1, 'Expected one durable queued job'
        assert all(job['status']=='queued' for job in store.status()), 'No SMTP worker may run'
        email_calls=[json.loads(c['result']) for c in calls if c['tool']=='email_send']
        assert sum(c['status']=='queued' for c in email_calls)==1
        assert all(c['status'] in {'queued','duplicate'} for c in email_calls), email_calls
        print('MODEL_SINGLE_CALL',len(email_calls)==1,'CALLS',len(email_calls),flush=True)
        if args.strict_single_call:
            assert len(email_calls)==1, 'Model repeated email_send; inspect duplicate receipt'
        print('PASS: one durable job, native scope and dedup verified; no SMTP/audio/control actions.',flush=True)
    finally:
        server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    main()
