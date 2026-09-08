#!/usr/bin/env python3
"""Native plugin discovery + real local Qwen; capture email unless --send-real."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--send-real',action='store_true')
parser.add_argument('--email-failure',action='store_true')
parser.add_argument('--search-failure',action='store_true')
parser.add_argument('--complete-request',action='store_true')
parser.add_argument('--live-code',action='store_true')
args=parser.parse_args()
if args.send_real and args.email_failure:
    parser.error('Cannot combine real mail and simulated failure')
repo=Path(__file__).resolve().parents[2]
sys.path.insert(0,'/home/agentdemo/.hermes/hermes-agent' if args.live_code else str(repo))
import yaml
from dotenv import dotenv_values
home=Path(tempfile.mkdtemp(prefix='hermes-workflow-check-'))
config=yaml.safe_load(Path('/home/agentdemo/.hermes/config.yaml').read_text())
config['model']={'default':'qwen3.6-35b-a3b','provider':'custom','base_url':'http://localhost:8000/v3',
                 'context_length':65536,'max_tokens':1024}
config['fallback_providers']=[]
config['plugins']={'enabled':['travel-voice']}
config['travel_voice']={'enabled':True,'default_recipient':'xiaoheng.hong@intel.com'}
config['memory']={'enabled':False}
(home/'config.yaml').write_text(yaml.safe_dump(config))
shutil.copytree(repo/'scripts/local-ovms/plugins/travel-voice',home/'plugins/travel-voice')
os.environ['HERMES_HOME']=str(home)
os.environ['NO_PROXY']=os.environ['no_proxy']='localhost,127.0.0.1'
for key,value in dotenv_values('/home/agentdemo/.hermes/.env').items():
    if value and (key in {'BRAVE_API_KEY','BRAVE_SEARCH_API_KEY'} or (args.send_real and key.startswith('EMAIL_'))):
        os.environ[key]=value
from run_agent import AIAgent
from hermes_cli import plugins
from hermes_cli.voice_response_policy import build_voice_turn_prefix
agent=AIAgent(model='qwen3.6-35b-a3b',provider='custom',base_url='http://localhost:8000/v3',
              api_key='local-ovms',api_mode='chat_completions',quiet_mode=True,max_iterations=8,
              enabled_toolsets=['web'],skip_memory=True,skip_context_files=True,platform='cli')
manager=plugins.get_plugin_manager()
callbacks=manager._hooks.get('run_turn_workflow',[])
assert len(callbacks)==1,'Native plugin was not loaded'
namespace=callbacks[0].__globals__
calls=[]
real_send=namespace['_send'];real_search=namespace['_search']
def send(recipient,body):
    calls.append({'tool':'email','recipient':recipient,'body':body})
    if args.send_real:
        assert recipient=='xiaoheng.hong@intel.com'
        return real_send(recipient,'HERMES-TRAVEL-WORKFLOW-TEST '+time.strftime('%Y%m%d-%H%M%S')+'\n\n'+body)
    return {'error':'Simulated failure'} if args.email_failure else {'success':True,'test_capture_only':True}
def search(query):
    calls.append({'tool':'search','query':query})
    return json.dumps({'error':'Simulated offline'}) if args.search_failure else real_search(query)
namespace['_send']=send;namespace['_search']=search
delta=[];agent.stream_delta_callback=delta.append
inputs=(["Plan seven days in San Francisco next month, departing from New York."] if args.complete_request else
        ["I'd like to travel to San Francisco. Could you give me some suggestions?",
         "I will go there probably in next month and I will travel there for like seven days and I will be traveling from New York."])
history=[];receipts=[]
for text in inputs:
    start=time.monotonic();offset=len(calls)
    result=agent.run_conversation(build_voice_turn_prefix(followup_enabled=True)+text,
           conversation_history=history,persist_user_message=text,input_modality='voice')
    history=result['messages']
    receipt={'input':text,'reply':result.get('final_response'),'completed':result.get('completed'),
             'reason':result.get('turn_exit_reason'),'seconds':round(time.monotonic()-start,2),
             'api_calls':result.get('api_calls'),'calls':calls[offset:]}
    receipts.append(receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k!='calls'},ensure_ascii=False),flush=True)
report=home/'receipt.json'
report.write_text(json.dumps(receipts,indent=2,ensure_ascii=False))
print('RECEIPT',report,flush=True)
assert not [d for d in delta if d],'Raw structured output reached the streaming display'
assert all(r['reason']=='plugin_workflow' and r['completed'] for r in receipts), 'Workflow did not complete'
if not args.complete_request:
    assert not receipts[0]['calls'] and receipts[0]['reply'].endswith('?')
assert sum(c['tool']=='search' for c in calls)<=2
mail=[c for c in calls if c['tool']=='email']
assert len(mail)==1 and mail[0]['recipient']=='xiaoheng.hong@intel.com'
assert all(f'Day {i}\n' in mail[0]['body'] for i in range(1,8))
assert 'New York → San Francisco → New York' in mail[0]['body']
assert len(receipts[-1]['reply'].split())<=85 and 'Day 1' not in receipts[-1]['reply']
if args.email_failure:
    assert 'not confirmed' in receipts[-1]['reply']
else:
    assert 'submitted for email delivery' in receipts[-1]['reply']
print('PASS native workflow, real local model, '+('real SMTP submission' if args.send_real else 'captured email'),flush=True)
