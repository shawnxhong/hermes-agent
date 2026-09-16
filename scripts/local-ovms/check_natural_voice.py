#!/usr/bin/env python3
"""Isolated local-model/native-loop acceptance; captured mail, no audio/tools."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
home = Path(tempfile.mkdtemp(prefix='hermes-natural-voice-'))
os.environ['HERMES_HOME'] = str(home)
os.environ['NO_PROXY'] = os.environ['no_proxy'] = 'localhost,127.0.0.1'
import yaml
(home/'config.yaml').write_text(yaml.safe_dump({
    'plugins': {'enabled':['general-voice']}, 'memory':{'enabled':False},
    'voice_delivery': {'enabled':True, 'default_recipient':'capture@example.com'},
}))
shutil.copytree(repo/'scripts/local-ovms/plugins/general-voice', home/'plugins/general-voice')
from run_agent import AIAgent
from hermes_state import SessionDB
from hermes_cli import general_voice, voice_continuity, voice_outbox, voice_presentation
from hermes_cli.voice_continuity_store import ContinuityStore

mail=[]
def capture(recipient, body):
    mail.append({'recipient':recipient, 'body':body})
    return {'success':True}
voice_outbox.kick=lambda:voice_outbox.drain(sender=capture)
routes=[]
original_route=voice_continuity.route
def traced_route(*args, **kwargs):
    value=original_route(*args, **kwargs);routes.append(value);return value
voice_continuity.route=traced_route
summaries=[]
original_summary=general_voice._summary
def traced_summary(*args, **kwargs):
    value=original_summary(*args, **kwargs);summaries.append(value);return value
general_voice._summary=traced_summary
agent=AIAgent(model='qwen3.6-35b-a3b', provider='custom',
    base_url='http://localhost:8000/v3',api_key='local-ovms',api_mode='chat_completions',
    quiet_mode=True,max_iterations=6,enabled_toolsets=[],skip_memory=True,
    skip_context_files=True,platform='cli',session_db=SessionDB(home/'sessions.db'))
cases=[
    ('greeting','How are you?',0,0),
    ('report','Draft a 250-word onboarding welcome email for new colleagues. Include a first-day checklist. Use a friendly tone.',1,None),
    ('revision','Revise that welcome email for remote colleagues and replace the first-day checklist with a three-day schedule.',2,None),
    ('explanation','Why did you recommend a three-day schedule? Answer briefly.',2,0),
    ('native','Write a Python function that returns the square of a number. Show the source code only.',2,None),
]
history=[];receipts=[]
for name,request,expected_mail,expected_summaries in cases:
    before=len(summaries);started=time.monotonic()
    token=voice_presentation.turn_presentation.set({'handled':False})
    try:
        value=agent.run_conversation(request,conversation_history=history,input_modality='voice')
        value=voice_presentation.finish_native(agent,value,request,agent.session_id)
    finally:voice_presentation.turn_presentation.reset(token)
    history=value.get('messages',history)
    row={'case':name,'reply':value.get('final_response'), 'mail_count':len(mail),
         'summary_calls':len(summaries)-before,'route':routes[-1] if routes else None,
         'completed':value.get('completed'),'seconds':round(time.monotonic()-started,2)}
    natural=not any(phrase in str(row['reply']).lower() for phrase in
                   ('the provided text', 'the assistant reports', 'the text lists', 'not a direct spoken reply'))
    row['passed']=(natural and bool(value.get('completed')) and len(mail)==expected_mail
                   and (expected_summaries is None or row['summary_calls']==expected_summaries))
    if name=='explanation':
        store=ContinuityStore();task=store.current(agent.session_id)
        row['report_preserved']=bool(task and store.result(agent.session_id,task['id'],task['artifact_version'])['body']==mail[-1]['body'])
        row['passed']=row['passed'] and row['report_preserved']
    receipts.append(row);print(json.dumps(row),flush=True)
    (home/'receipt.json').write_text(json.dumps(receipts,indent=2))
print('Receipt:',home/'receipt.json',flush=True)
assert all(row['passed'] for row in receipts), 'Live acceptance failed; inspect receipt before deployment'
assert mail[0]['body'] != mail[1]['body'], 'Revision must email its new version'
print('PASS: real native loop/local model, captured email only; acoustic quality still needs human testing.')
