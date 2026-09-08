#!/usr/bin/env python3
"""Real local model/native harness replay, with captured mail only."""
import argparse,json,os,shutil,sys,tempfile,time
from pathlib import Path
import yaml

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--live-code',action='store_true')
parser.add_argument('--code-path',type=Path,help='Read-only staged runtime overlay to validate before deployment')
parser.add_argument('--native-tools',action='store_true',help='Use native default schemas, with a test-only side-effect blocker')
parser.add_argument('--scenario',choices=['general','general-variant','travel'],default='general')
parser.add_argument('--email-failure',action='store_true')
args=parser.parse_args()
repo=Path(__file__).resolve().parents[2]
code=args.code_path or (Path('/home/agentdemo/.hermes/hermes-agent') if args.live_code else repo)
sys.path.insert(0,str(code))
home=Path(tempfile.mkdtemp(prefix='hermes-general-voice-'))
cfg=yaml.safe_load(Path('/home/agentdemo/.hermes/config.yaml').read_text())
cfg['plugins']={'enabled':['general-voice','travel-voice']}
cfg['voice_delivery']={'enabled':True,'default_recipient':'xiaoheng.hong@intel.com'}
cfg['memory']={'enabled':False}
(home/'config.yaml').write_text(yaml.safe_dump(cfg))
for name in ('general-voice','travel-voice'):
    source=Path('/home/agentdemo/.hermes/plugins')/name if args.live_code else repo/'scripts/local-ovms/plugins'/name
    shutil.copytree(source,home/'plugins'/name)
os.environ['HERMES_HOME']=str(home)
os.environ['NO_PROXY']=os.environ['no_proxy']='localhost,127.0.0.1'
from run_agent import AIAgent
from hermes_state import SessionDB
from hermes_cli import general_voice
from hermes_cli.voice_delivery import TaskStore
from hermes_cli.voice_response_policy import build_voice_turn_prefix
mail=[]
def capture(recipient,body):
    mail.append({'recipient':recipient,'body':body})
    return {'error':'simulated SMTP failure'} if args.email_failure else {'success':True}
general_voice._send=capture
routes=[]
original_router=general_voice.route_task
def traced_router(*a,**kw):
    result=original_router(*a,**kw)
    routes.append(result)
    print('ROUTE '+json.dumps(result),flush=True)
    return result
general_voice.route_task=traced_router
general_voice.travel_plugin()._send=capture
agent=AIAgent(model='qwen3.6-35b-a3b',provider='custom',base_url='http://localhost:8000/v3',api_key='local-ovms',
    api_mode='chat_completions',quiet_mode=True,max_iterations=12,enabled_toolsets=None if args.native_tools else ['web'],
    skip_memory=True,skip_context_files=True,platform='cli',session_db=SessionDB(home/'sessions.db'))
delta=[];agent.stream_delta_callback=delta.append
from hermes_cli import plugins
if args.native_tools:
    def test_side_effect_guard(**kw):
        if kw['tool_name'] not in {'web_search','web_extract'}:
            return {'action':'block','message':'This capture-only test forbids external side effects. Return the requested draft as text.'}
    plugins.get_plugin_manager()._hooks.setdefault('pre_tool_call',[]).append(test_side_effect_guard)
for callback in plugins.get_plugin_manager()._hooks.get('run_turn_workflow',[]):
    if 'FACT_PROMPT' in callback.__globals__:
        callback.__globals__['_send']=capture
cases=[('simple','What is two plus two? Answer briefly.','voice'),
       ('ask','Help me plan a team workshop.','voice'),
       ('execute','It is for twenty marketing colleagues, for one hour, to practice clear project updates. Include three activities and facilitator notes.','voice'),
       ('explain','Why are interactive exercises useful for this workshop? Please answer briefly.','voice'),
       ('redirect','Could you send the report to a different email address? I will type it.','voice'),
       ('mailbox','791633252@qq.com','text'),
       ('new_task','Now draft a 200-word welcome message for new employees, with a friendly tone.','voice')]
if args.scenario=='general-variant':
    cases=[('simple','What does RSVP mean? Answer briefly.','voice'),
           ('ask','Help me prepare a customer product launch event.','voice'),
           ('execute','Forty US business partners, a two-hour virtual session, to introduce a new collaboration app. Include a timed agenda and speaker notes.','voice'),
           ('explain','Why should this event include time for audience questions? Answer briefly.','voice'),
           ('redirect','Please send the report to another email address.','voice'),
           ('mailbox','791633252@qq.com','text'),
           ('new_task','Now create a short internal memo explaining a Friday office closure for maintenance.','voice')]
if args.scenario=='travel':
    cases=[('travel_ask','I want to travel to New York. Could you give me some suggestions?','voice'),
           ('travel_plan','I will travel there in December for four days from Vancouver.','voice'),
           ('travel_explain','Why is flying the best way to get there? Please answer briefly.','voice'),
           ('travel_redirect','Please send the itinerary to another email address.','voice'),
           ('travel_mailbox','791633252@qq.com','text')]
history=[];receipts=[]
for name,text,modality in cases:
    offset=len(mail);deltas=len(delta);start=time.monotonic()
    previous_tools=sum(m.get('role')=='tool' for m in history)
    message=build_voice_turn_prefix(followup_enabled=True)+text if modality=='voice' else text
    result=agent.run_conversation(message,persist_user_message=text,input_modality=modality,conversation_history=history)
    tool_count=sum(m.get('role')=='tool' for m in result['messages'])-previous_tools
    history=result['messages'];task=TaskStore().current(agent.session_id)
    row={'case':name,'reply':result['final_response'],'completed':result['completed'],'reason':result['turn_exit_reason'],
         'calls':result['api_calls'],'seconds':round(time.monotonic()-start,2),'emails':len(mail)-offset,
         'task_id':task['id'] if task else None,'raw_streamed':any(delta[deltas:]),'tools':tool_count}
    receipts.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
(home/'receipt.json').write_text(json.dumps({'turns':receipts,'mail':mail,'routes':routes},indent=2,ensure_ascii=False))
print('RECEIPT',home/'receipt.json',flush=True)
assert all(r['completed'] and not r['raw_streamed'] for r in receipts)
if args.scenario=='travel':
    assert receipts[0]['reply'].endswith('?') and receipts[0]['emails']==0
    assert receipts[1]['emails']==1 and receipts[2]['emails']==0
    assert receipts[3]['calls']==receipts[4]['calls']==0
    assert len(mail)==2 and mail[0]['body']==mail[1]['body']
    print('PASS travel strategy, native explanation, adopted artifact and typed resend; mail captured',flush=True)
    sys.exit(0)
assert receipts[0]['emails']==0 and receipts[1]['emails']==0 and receipts[1]['reply'].endswith('?')
assert receipts[2]['emails']==1 and receipts[3]['emails']==0
assert receipts[1]['task_id']==receipts[2]['task_id']
assert receipts[2]['task_id']==receipts[3]['task_id']==receipts[5]['task_id']
assert receipts[4]['calls']==receipts[5]['calls']==0 and receipts[4]['reply'].endswith('?')
assert receipts[5]['emails']==1 and mail[0]['body']==mail[1]['body'] and mail[1]['recipient']=='791633252@qq.com'
assert receipts[6]['task_id']!=receipts[2]['task_id'] and receipts[6]['emails']==1
assert mail[2]['recipient']=='xiaoheng.hong@intel.com'
assert all(len(m['body'].split())>=25 and 'NO_REPLY_EXPECTED' not in m['body'] for m in mail)
assert all(len(r['reply'].split())<=85 for r in receipts)
assert all(r['tools']==0 for r in receipts),'Self-contained drafting/explanation must not need external tools'
if args.email_failure:
    assert all('not confirmed' in r['reply'] for r in receipts if r['emails'])
print('PASS native general voice execution, artifact follow-up, typed resend and new-task isolation; all mail captured',flush=True)
