#!/usr/bin/env python3
"""Real local-Qwen skill check in an isolated home; email is always captured."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--surface', choices=['voice','im'], default='voice')
parser.add_argument('--search-failure', action='store_true')
parser.add_argument('--email-failure', action='store_true')
parser.add_argument('--complete-request', action='store_true')
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
home = Path(tempfile.mkdtemp(prefix='hermes-travel-check-'))
import yaml
from dotenv import dotenv_values
config = yaml.safe_load(Path('/home/agentdemo/.hermes/config.yaml').read_text())
config['model'] = dict(default='qwen3.6-35b-a3b', provider='custom',
                      base_url='http://localhost:8000/v3',context_length=65536,max_tokens=2048)
config['fallback_providers'] = []
config['platform_toolsets'] = {}
config['memory'] = {'enabled':False}
config.setdefault('tools', {}).setdefault('tool_search', {})['enabled'] = 'off'
config['voice']['followup'] = {'enabled':True}
(home/'config.yaml').write_text(yaml.safe_dump(config))
shutil.copytree(repo/'skills/productivity/travel-concierge',home/'skills/travel-concierge')
os.environ['HERMES_HOME'] = str(home)
os.environ['NO_PROXY'] = 'localhost,127.0.0.1'
os.environ['no_proxy'] = 'localhost,127.0.0.1'
for key,value in dotenv_values('/home/agentdemo/.hermes/.env').items():
    if key in {'BRAVE_API_KEY','BRAVE_SEARCH_API_KEY'} and value:
        os.environ[key]=value
os.environ['EMAIL_ADDRESS']='test-sender@example.com'
os.environ['EMAIL_PASSWORD']='test-only-never-used'
os.environ['EMAIL_SMTP_HOST']='invalid.example'
from run_agent import AIAgent
from tools.registry import registry
from hermes_cli.voice_response_policy import build_voice_turn_prefix
from agent.skill_commands import build_preloaded_skills_prompt
skill_prompt,loaded,missing=build_preloaded_skills_prompt(['travel-concierge'])
assert loaded==['travel-concierge'] and not missing

calls=[]
def wrap(entry):
    original=entry.handler
    def handler(params,**kw):
        calls.append({'name':entry.name,'args':params})
        print('TOOL',entry.name,flush=True)
        if entry.name=='send_message':
            if params.get('action')!='send' or params.get('target')!='email:demo@example.com':
                return json.dumps({'error':'Invalid send action or test recipient; nothing sent'})
            return json.dumps({'error':'Simulated delivery failure'} if args.email_failure else
                              {'success':True,'platform':'email','test_capture_only':True})
        if entry.name in {'web_search','web_extract'}:
            if args.search_failure:
                return json.dumps({'error':'Simulated network unavailable. Do not retry.'})
            # Stop the evaluation from issuing unlimited real network calls.
            cap=2 if entry.name=='web_search' else 1
            if sum(c['name']==entry.name for c in calls)>cap:
                return json.dumps({'error':'Evaluation network-call budget exceeded'})
            return original(params,**kw)
        if entry.name in {'skill_view','skills_list'}:
            return original(params,**kw)
        return json.dumps({'error':'Tool blocked in isolated skill evaluation'})
    return handler

import tools.send_message_tool
# The test home intentionally has no live gateway. Expose the captured sender
# as the deployed CLI sees it when its real gateway is running.
registry.get_entry('send_message').check_fn=lambda:True
a=AIAgent(model='qwen3.6-35b-a3b',provider='custom',base_url='http://localhost:8000/v3',
    api_key='local-ovms',api_mode='chat_completions',quiet_mode=True,max_iterations=8,
    request_overrides={'temperature':0},
    enabled_toolsets=['web','voice_delivery'] if args.surface=='voice' else ['web'],
    skip_memory=True,skip_context_files=True,ephemeral_system_prompt=skill_prompt,
    platform='cli' if args.surface=='voice' else 'feishu')
for entry in registry._snapshot_entries():
    entry.handler=wrap(entry)
print('EXPOSED_TOOLS',sorted(a.valid_tool_names),flush=True)
assert args.surface!='voice' or 'send_message' in a.valid_tool_names
inputs=(['Plan a three-day trip to San Francisco in October from Seattle. '
         + ('Email the details to demo@example.com.' if args.surface=='voice' else 'Put the full itinerary here.')]
        if args.complete_request else
        ['I would like to visit San Francisco.','In October, for three days, from Seattle. '
         + ('Email the details to demo@example.com.' if args.surface=='voice' else 'Put the full itinerary here.')])
history=[];receipts=[]
for message in inputs:
    start=time.monotonic();first=len(calls)
    prefix=build_voice_turn_prefix(followup_enabled=True) if args.surface=='voice' else ''
    result=a.run_conversation(user_message=prefix+message,conversation_history=history,
                              persist_user_message=message)
    history=result.get('messages',[])
    receipt={'input':message,'reply':result.get('final_response'),
             'seconds':round(time.monotonic()-start,2),'calls':calls[first:]}
    receipts.append(receipt);print(json.dumps(receipt,ensure_ascii=False),flush=True)
report=home/'receipt.json';report.write_text(json.dumps(receipts,indent=2,ensure_ascii=False))
print('RECEIPT',report,flush=True)
assert not any(c['name']=='clarify' for c in calls),'Unexpected clarify'
assert not any(c['name']=='skill_manage' for c in calls),'Model attempted to rewrite the skill'
if not args.complete_request:
    assert not any(c['name'] in {'web_search','web_extract','send_message'} for c in receipts[0]['calls']),'Orientation used costly tools'
    assert receipts[0]['reply'].rstrip().endswith('?'),'First reply is not a final question'
for name,cap in [('web_search',2),('web_extract',1),('send_message',1)]:
    assert sum(c['name']==name for c in calls)<=cap, name+' exceeded workflow budget'
if args.surface=='voice':
    assert sum(c['name']=='send_message' for c in calls)==1,'Expected captured itinerary email'
    sent = next(c['args'] for c in calls if c['name']=='send_message')
    assert sent.get('action')=='send' and sent.get('target')=='email:demo@example.com','Invalid email parameters'
    assert len(sent.get('message','').split())>=150,'Incomplete itinerary email'
    assert all(len(r['reply'].split())<=100 for r in receipts),'Voice reply too long'
else:
    assert not any(c['name']=='send_message' for c in calls),'IM unexpectedly emailed'
print('PASS: structural checks only; review factual grounding and delivery wording manually. Mail captured, not sent.',flush=True)
