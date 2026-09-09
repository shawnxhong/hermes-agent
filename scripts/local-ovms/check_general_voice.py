#!/usr/bin/env python3
"""Real local model/native harness replay, with captured mail only."""
import argparse,hashlib,json,os,shutil,sys,tempfile,time
from pathlib import Path
import yaml

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--live-code',action='store_true')
parser.add_argument('--code-path',type=Path,help='Read-only staged runtime overlay to validate before deployment')
parser.add_argument('--native-tools',action='store_true',help='Use native default schemas, with a test-only side-effect blocker')
parser.add_argument('--scenario',choices=['general','general-variant','travel','travel-sydney','travel-sydney-fragment','continuity-seoul'],default='general')
parser.add_argument('--continuity',action='store_true')
parser.add_argument('--email-failure',action='store_true')
parser.add_argument('--stop-after',type=int)
parser.add_argument('--temperature',type=float)
args=parser.parse_args()
repo=Path(__file__).resolve().parents[2]
code=args.code_path or (Path('/home/agentdemo/.hermes/hermes-agent') if args.live_code else repo)
sys.path.insert(0,str(code))
home=Path(tempfile.mkdtemp(prefix='hermes-general-voice-'))
cfg=yaml.safe_load(Path('/home/agentdemo/.hermes/config.yaml').read_text())
cfg['plugins']={'enabled':['general-voice','travel-voice']}
cfg['voice_delivery']={'enabled':True,'default_recipient':'xiaoheng.hong@intel.com'}
if args.continuity:cfg['voice_delivery']['continuity']={'enabled':True}
cfg['memory']={'enabled':False}
(home/'config.yaml').write_text(yaml.safe_dump(cfg))
for name in ('general-voice','travel-voice'):
    source=Path('/home/agentdemo/.hermes/plugins')/name if args.live_code else repo/'scripts/local-ovms/plugins'/name
    shutil.copytree(source,home/'plugins'/name)
os.environ['HERMES_HOME']=str(home)
os.environ['NO_PROXY']=os.environ['no_proxy']='localhost,127.0.0.1'
# Real search acceptance must use the configured search credential, not a
# credential-free temporary home. Never print or copy the value into artifacts.
if args.continuity:
    from dotenv import dotenv_values
    brave=dotenv_values('/home/agentdemo/.hermes/.env').get('BRAVE_SEARCH_API_KEY')
    if brave:os.environ['BRAVE_SEARCH_API_KEY']=brave
from run_agent import AIAgent
from hermes_state import SessionDB
from hermes_cli import general_voice
from hermes_cli.voice_delivery import TaskStore
from hermes_cli.voice_response_policy import build_voice_turn_prefix
from hermes_cli.tools_config import _get_platform_tools
from openai.resources.chat.completions import Completions
original_create=Completions.create
api_timings=[]
def timed_create(self,*a,**kw):
    started=time.monotonic()
    result=original_create(self,*a,**kw)
    row={'seconds':round(time.monotonic()-started,2),'tools':len(kw.get('tools',[])),
         'schema':kw.get('response_format',{}).get('json_schema',{}).get('name'),
         'usage':str(getattr(result,'usage',None))}
    api_timings.append(row)
    print('API '+json.dumps(row),flush=True)
    return result
Completions.create=timed_create
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
if args.continuity:
    from hermes_cli import voice_continuity
    original_router=voice_continuity.route
    voice_continuity.route=traced_router
general_voice.travel_plugin()._send=capture
agent=AIAgent(model='qwen3.6-35b-a3b',provider='custom',base_url='http://localhost:8000/v3',api_key='local-ovms',
    api_mode='chat_completions',quiet_mode=True,max_iterations=12,
    enabled_toolsets=sorted(_get_platform_tools(cfg,'cli')) if args.native_tools else ['web'],
    ephemeral_system_prompt=cfg.get('agent',{}).get('system_prompt'),
    skip_memory=True,skip_context_files=True,platform='cli',session_db=SessionDB(home/'sessions.db'))
delta=[];agent.stream_delta_callback=delta.append
wires=[]
original_kwargs=agent._build_api_kwargs
def traced_kwargs(*a,**kw):
    result=original_kwargs(*a,**kw)
    if args.temperature is not None:result['temperature']=args.temperature
    wires.append({k:result[k] for k in ('messages','tools','tool_choice','temperature','max_tokens','extra_body') if k in result})
    (home/'wire.json').write_text(json.dumps(wires,ensure_ascii=False,indent=2))
    print('WIRE '+json.dumps({'tools':len(result.get('tools',[])),'temperature':result.get('temperature'),
          'context_present':'Current-turn execution context' in str(result.get('messages')),
          'chars':len(str(result.get('messages')))}),flush=True)
    return result
agent._build_api_kwargs=traced_kwargs
from hermes_cli import plugins
if args.native_tools:
    from tools.tool_search import TOOL_SEARCH_NAME, TOOL_DESCRIBE_NAME
    def test_side_effect_guard(**kw):
        if kw['tool_name'] not in {'web_search','web_extract',TOOL_SEARCH_NAME,TOOL_DESCRIBE_NAME}:
            return {'action':'block','message':'This capture-only test forbids external side effects. Return the requested draft as text.'}
    plugins.get_plugin_manager()._hooks.setdefault('pre_tool_call',[]).append(test_side_effect_guard)
for callback in plugins.get_plugin_manager()._hooks.get('run_turn_workflow',[]):
    if 'FACT_PROMPT' in callback.__globals__:
        callback.__globals__['_send']=capture
cases=[('simple','What does RSVP mean? Answer briefly.','voice'),
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
if args.scenario.startswith('travel'):
    cases=[('travel_ask','I want to travel to New York. Could you give me some suggestions?','voice'),
           ('travel_plan','I will travel there in December for four days from Vancouver.','voice'),
           ('travel_explain','Why is flying the best way to get there? Please answer briefly.','voice'),
           ('travel_redirect','Please send the itinerary to another email address.','voice'),
           ('travel_mailbox','791633252@qq.com','text')]
if args.scenario.startswith('travel-sydney'):
    cases[0]=('travel_ask','I wanna go travel to Sydney. Could you give me some suggestions?','voice')
    cases[1]=('travel_plan',"I will be traveling there in December and I will be having like one week and I'll be traveling from Melbourne.",'voice')
if args.scenario=='travel-sydney-fragment':
    cases.insert(1,('fragment','Um, my badge is like...','voice'))
if args.scenario=='continuity-seoul':
    assert args.continuity
    cases=[('names','Could you suggest some famous Korean BBQ restaurants in Seoul? Just name four.','voice'),
           ('details','Could you give me the details of those restaurants you recommended, sent to me by email?','voice'),
           ('invalid','My email address is xiaoheng.hong.intel.com. Send those details there.','voice'),
           ('yes','Yes, I mean that. Could you just send me the email?','voice'),
           ('yes_again','Yes, I mean that. Just send me the email.','voice'),
           ('redirect','Please send the report to another email address.','voice'),
           ('mailbox','791633252@qq.com','text'),
           ('switch','What does RSVP mean? Answer briefly.','voice'),
           ('return','Return to those Seoul restaurants. Email me the same detailed report.','voice')]
history=[];receipts=[]
if args.stop_after:cases=cases[:args.stop_after]
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
(home/'receipt.json').write_text(json.dumps({'turns':receipts,'mail':mail,'routes':routes,'api_timings':api_timings},indent=2,ensure_ascii=False))
print('RECEIPT',home/'receipt.json',flush=True)
if args.stop_after:sys.exit(0)
system_hashes={hashlib.sha256(json.dumps([m for m in w['messages'] if m['role']=='system'],sort_keys=True).encode()).hexdigest() for w in wires}
tool_hashes={hashlib.sha256(json.dumps(w.get('tools'),sort_keys=True).encode()).hexdigest() for w in wires}
assert len(system_hashes)==len(tool_hashes)==1,'System prompt and tool schemas must remain byte-stable across this replay'
assert all(r['completed'] and not r['raw_streamed'] for r in receipts)
if args.scenario=='continuity-seoul':
    by={r['case']:r for r in receipts}
    assert by['names']['emails']==0 and by['details']['emails']==1
    assert len(mail[0]['body'].split())>len(by['names']['reply'].split())*2,'Email must enrich the list, not merely resend it'
    assert by['names']['task_id']==by['details']['task_id']==by['return']['task_id']
    assert by['switch']['task_id']!=by['details']['task_id']
    assert by['invalid']['reply'].endswith('?') and by['invalid']['emails']==0
    assert by['yes']['calls']==by['yes_again']['calls']==by['mailbox']['calls']==0
    assert by['yes_again']['emails']==0 and len(mail)==2
    assert mail[0]['body']==mail[1]['body'] and mail[1]['recipient']=='791633252@qq.com'
    assert all(len(r['reply'].split())<=85 for r in receipts)
    print('PASS Seoul short result, enriched email, one-shot confirmation, recipient override and old-topic return; mail captured')
    sys.exit(0)
if args.scenario.startswith('travel'):
    fragments=[r for r in receipts if r['case']=='fragment']
    assert all(r['calls']==0 and r['emails']==0 and r['reply'].endswith('?') for r in fragments)
    receipts=[r for r in receipts if r['case']!='fragment']
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
