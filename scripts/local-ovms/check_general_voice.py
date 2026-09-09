#!/usr/bin/env python3
"""Real local model/native harness replay, with captured mail only."""
import argparse,hashlib,json,os,re,shutil,sys,tempfile,time
from pathlib import Path
import yaml

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--live-code',action='store_true')
parser.add_argument('--code-path',type=Path,help='Read-only staged runtime overlay to validate before deployment')
parser.add_argument('--native-tools',action='store_true',help='Use native default schemas, with a test-only side-effect blocker')
parser.add_argument('--scenario',choices=['general','general-variant','travel','travel-sydney','travel-sydney-fragment','continuity-seoul','continuity-mixed','continuity-travel-mixed','continuity-melbourne','continuity-safety','continuity-failure','continuity-isolation'],default='general')
parser.add_argument('--generation-failure',action='store_true')
parser.add_argument('--search-failure',action='store_true')
parser.add_argument('--seed',type=int,default=1,help='Reproducible mixed-topic order')
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
cfg['plugins']={'enabled':['general-voice']}
cfg['providers']={'custom':{
    'base_url':'http://localhost:8000/v3','api_key':'local-ovms',
    'default_model':'qwen3.6-35b-a3b','request_timeout_seconds':90,
    'stale_timeout_seconds':90}}
cfg['voice_delivery']={'enabled':True,'default_recipient':'xiaoheng.hong@intel.com'}
if args.continuity:cfg['voice_delivery']['continuity']={'enabled':True}
cfg['memory']={'enabled':False}
(home/'config.yaml').write_text(yaml.safe_dump(cfg))
for name in ('general-voice',):
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
fault_active=False
def timed_create(self,*a,**kw):
    started=time.monotonic()
    result=original_create(self,*a,**kw)
    if args.generation_failure and fault_active and kw.get('tools') and not kw.get('stream'):
        for choice in result.choices:choice.finish_reason='length'
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
summary_checks=[]
original_summary=general_voice._summary
def checked_summary(agent,body,language,evidence=None,task_request=None):
    record={'body':body,'evidence':evidence,'request':task_request};summary_checks.append(record)
    try:
        value=original_summary(agent,body,language,evidence,task_request)
        record.update(summary=value,valid=True)
        return value
    except Exception as error:
        record.update(valid=False,error=type(error).__name__)
        raise
general_voice._summary=checked_summary
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
agent=AIAgent(model='qwen3.6-35b-a3b',provider='custom',base_url='http://localhost:8000/v3',api_key='local-ovms',
    api_mode='chat_completions',quiet_mode=True,max_iterations=12,
    enabled_toolsets=sorted(_get_platform_tools(cfg,'cli')) if args.native_tools else ['web'],
    ephemeral_system_prompt=None,
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
        if args.search_failure and fault_active and kw['tool_name'] in {'web_search','web_extract'}:
            return {'action':'block','message':'Simulated search service failure. No verified results are available; do not guess or repeat this operation.'}
        if kw['tool_name'] not in {'web_search','web_extract',TOOL_SEARCH_NAME,TOOL_DESCRIBE_NAME}:
            return {'action':'block','message':'This capture-only test forbids external side effects. Return the requested draft as text.'}
    plugins.get_plugin_manager()._hooks.setdefault('pre_tool_call',[]).append(test_side_effect_guard)
if args.scenario=='continuity-isolation':
    from hermes_cli.voice_continuity_store import ContinuityStore
    store=ContinuityStore();old=store.start(agent.session_id,'A previous restaurant report')
    store.save_result(agent.session_id,old,body='Earlier saved report.',summary='Earlier summary.',detailed=True)
    for repeat in range(3):
        for platform,modality in [('feishu','text'),('feishu','voice'),('cli','text')]:
            before=len(api_timings)
            value=general_voice.run_workflow(agent=agent,user_message='Write a detailed project report.',session_id=agent.session_id,input_modality=modality,platform=platform)
            assert value is None and len(api_timings)==before
        for text in ['Write a Python function that sorts a list.', 'Fix this Python error: TypeError: list object is not callable.', 'Create a file named example.txt with the text hello.']:
            value=general_voice.run_workflow(agent=agent,user_message=text,session_id=agent.session_id,input_modality='voice',platform='cli')
            assert value is None,'Coding/actions must return to the original native harness'
            before=len(api_timings)
            for answer in ('Yes','No, cancel.'):
                assert general_voice.run_workflow(agent=agent,user_message=answer,session_id=agent.session_id,input_modality='voice',platform='cli') is None
            assert len(api_timings)==before,'Native follow-up confirmations must not be consumed by email continuity'
    assert not mail
    print('PASS three installed-routing repetitions: IM/typed zero model calls; coding/actions native pass-through; no external writes')
    sys.exit(0)
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
if args.scenario=='continuity-mixed':
    import random
    assert args.continuity
    cases=[
        ('meeting','Draft a 150-word agenda for a one-hour project kickoff with six engineers. Include goals, roles and next steps. Use reasonable assumptions; no questions.','voice'),
        ('memo','Draft a 150-word internal memo announcing that the office will close Friday for maintenance. Staff should work remotely. Use placeholders for unknown names.','voice'),
        ('shopping','Make a detailed 150-word buying checklist comparing a backpack and a rolling suitcase for weekly train commuting. No brands or current prices; use reasonable assumptions.','voice'),
        ('rsvp','What does RSVP mean? One sentence please.','voice'),
        ('sky','Why does the sky look blue? Answer briefly.','voice'),
        ('math','How many minutes are in two and a half hours? Just the answer.','voice')]
    random.Random(args.seed).shuffle(cases)
    cases += [('return_meeting','Return to the project kickoff agenda. Email me that same agenda.','voice'),
              ('revise_meeting','Change that kickoff agenda to thirty minutes instead of one hour. Keep the same six engineers. Do not email this revision yet.','voice'),
              ('new_question','What is the difference between a metaphor and a simile? Briefly please.','voice'),
              ('send_revision','Email me the revised thirty-minute project kickoff agenda.','voice'),
              ('pending_address','Send that revised agenda to xiaoheng.hong.intel.com.','voice'),
              ('interrupt_question','How many sides does a hexagon have? Just the answer.','voice'),
              ('stale_yes','Yes, I mean that. Just send me the email.','voice')]
if args.scenario=='continuity-travel-mixed':
    assert args.continuity
    cases=[('trip_ask','I want to visit New York, traveling from Vancouver. Any suggestions?','voice'),
           ('interrupt','How many minutes are in two and a half hours? Just the answer.','voice'),
           ('trip_details','Back to my New York trip: December, for four days.','voice'),
           ('memo','Draft a short memo announcing Friday office closure for maintenance. Everyone should work from home. Use placeholders for unknown names.','voice'),
           ('trip_return','Email me the same New York itinerary you prepared.','voice'),
           ('trip_explain','Why did you recommend those places in New York? Answer briefly; no email.','voice'),
           ('new_question','What does RSVP mean? Answer briefly.','voice')]
if args.scenario=='continuity-melbourne':
    assert args.continuity
    cases=[('trip_ask','I want to go travel to Melbourne. Could you give me some advice on that?','voice'),
           ('trip_details','I will be traveling from Sydney and I will be going there in October this year and I think I have five days','voice'),
           ('new_question','Why does the sky look blue? Answer briefly.','voice'),
           ('trip_return','Return to my Melbourne trip and explain the transportation recommendation briefly. Do not email again.','voice')]
if args.scenario=='continuity-safety':
    assert args.continuity
    cases=[('engineering','Draft a 100-word engineering kickoff agenda for six developers, one hour, on a new app. Use reasonable assumptions.','voice'),
           ('marketing','Draft a separate 100-word marketing kickoff agenda for a product launch, four marketers, one hour. Use reasonable assumptions.','voice'),
           ('ambiguous',"Email one of those two agendas to 791633252@qq.com. I haven't decided which one.",'voice'),
           ('choose','The engineering agenda.','voice'),
           ('redirect','Send that agenda to another email address.','voice'),
           ('fragment','Um, my address is...','voice'),
           ('fragment_again','Um...','voice'),
           ('stale_yes','Yes','voice'),
           ('redirect_again','Send the engineering agenda to another email address.','voice'),
           ('cancel','No, cancel.','voice')]
if args.scenario=='continuity-failure':
    assert args.continuity and (args.email_failure or args.generation_failure or args.search_failure)
    cases=[('failure','Draft a concise but complete 150-word welcome memo for new employees joining an engineering team. Use reasonable assumptions.','voice')]
    if args.search_failure:
        cases=[('failure',"Search the web for today's weather forecast in Boston and prepare a detailed report. Only use verified search results; do not guess.",'voice')]
    if args.email_failure:
        cases.append(('repeat','Email that same welcome memo to me again.','voice'))
    cases.append(('recovery','How many minutes are in two hours? Just the answer.','voice'))
history=[];receipts=[]
if args.stop_after:cases=cases[:args.stop_after]
for name,text,modality in cases:
    fault_active=name=='failure'
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
(home/'receipt.json').write_text(json.dumps({'turns':receipts,'mail':mail,'routes':routes,'api_timings':api_timings,'summary_checks':summary_checks},indent=2,ensure_ascii=False))
print('RECEIPT',home/'receipt.json',flush=True)
if args.stop_after:sys.exit(0)
system_hashes={hashlib.sha256(json.dumps([m for m in w['messages'] if m['role']=='system'],sort_keys=True).encode()).hexdigest() for w in wires}
tool_hashes={hashlib.sha256(json.dumps(w.get('tools'),sort_keys=True).encode()).hexdigest() for w in wires}
assert len(system_hashes)==len(tool_hashes)==1,'System prompt and tool schemas must remain byte-stable across this replay'
assert all(not r['raw_streamed'] and (r['completed'] or ((args.generation_failure or args.search_failure) and r['case']=='failure')) for r in receipts)
if args.scenario=='continuity-failure':
    by={r['case']:r for r in receipts}
    assert by['recovery']['completed'] and '120' in by['recovery']['reply'] and by['recovery']['emails']==0
    if args.email_failure:
        assert len(mail)==1 and by['repeat']['emails']==0
        assert all('submitted' not in by[k]['reply'].lower() for k in ('failure','repeat'))
    elif args.search_failure:
        assert by['failure']['completed'] and not mail
        assert 'No automatic email was sent' in by['failure']['reply']
        assert by['failure']['tools']<=2
    else:
        assert not by['failure']['completed'] and not mail
        assert by['failure']['tools']<=6
    print('PASS bounded failure, truthful delivery status and independent next-turn recovery; mail captured')
    sys.exit(0)
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
    from hermes_cli.voice_continuity_store import ContinuityStore
    saved=ContinuityStore().result(agent.session_id,by['details']['task_id'])
    cited={url.rstrip('.,;') for url in re.findall(r'https?://[^\s<>"\]\)]+',mail[0]['body'])}
    known={url.rstrip('.,;') for url in saved['sources']}
    assert cited and cited<=known,'Research email links must come from successful tool evidence for this result'
    print('PASS Seoul short result, enriched email, one-shot confirmation, recipient override and old-topic return; mail captured')
    sys.exit(0)
if args.scenario=='continuity-safety':
    by={r['case']:r for r in receipts}
    assert by['engineering']['task_id']!=by['marketing']['task_id']
    assert by['ambiguous']['reply'].endswith('?') and by['ambiguous']['emails']==0
    assert by['choose']['task_id']==by['engineering']['task_id'] and by['choose']['emails']==1
    assert mail[2]['body']==mail[0]['body'] and mail[2]['recipient']=='791633252@qq.com'
    assert by['fragment']['calls']==by['fragment_again']['calls']==by['stale_yes']['calls']==0
    assert not by['fragment_again']['reply'].endswith('?')
    assert all(by[k]['emails']==0 for k in ('redirect','fragment','fragment_again','stale_yes','redirect_again','cancel'))
    assert len(mail)==3 and all(len(r['reply'].split())<=85 for r in receipts)
    print('PASS ambiguous reference, chosen artifact, fragment pause and cancellation; mail captured')
    sys.exit(0)
if args.scenario=='continuity-mixed':
    by={r['case']:r for r in receipts}
    assert len({by[k]['task_id'] for k in ('meeting','memo','shopping','rsvp','sky','math','new_question')})==7,'Independent tasks must not overwrite each other'
    assert all(by[k]['emails']==1 for k in ('meeting','memo','shopping')),'Complete new complex tasks should execute and email without optional questions'
    assert all(by[k]['emails']==0 for k in ('rsvp','sky','math','new_question','return_meeting','revise_meeting')),'No inherited email authority or repeated submission'
    assert all(by[k]['task_id']==by['meeting']['task_id'] for k in ('return_meeting','revise_meeting','send_revision')),'References must return to the right task'
    assert by['send_revision']['emails']==1 and len(mail)==4
    assert '30' in mail[-1]['body'] or 'thirty' in mail[-1]['body'].lower(),'Must send revised agenda, not the original'
    assert all(m['recipient']=='xiaoheng.hong@intel.com' for m in mail)
    assert by['pending_address']['reply'].endswith('?') and by['pending_address']['emails']==0
    assert by['pending_address']['task_id']==by['meeting']['task_id'],'Pending delivery must bind the agenda, not merely avoid sending yet'
    assert by['interrupt_question']['task_id']!=by['meeting']['task_id'] and by['interrupt_question']['emails']==0
    assert by['stale_yes']['calls']==0 and by['stale_yes']['emails']==0
    assert all(len(r['reply'].split())<=85 and (r['case']=='pending_address' or not r['reply'].endswith('?')) for r in receipts),'No optional repeated questions or long TTS'
    print('PASS mixed-topic continuity seed',args.seed,'; mail captured')
    sys.exit(0)
if args.scenario=='continuity-travel-mixed':
    by={r['case']:r for r in receipts}
    assert by['trip_ask']['reply'].endswith('?') and by['trip_ask']['emails']==0
    assert all(by[k]['task_id']==by['trip_ask']['task_id'] for k in ('trip_details','trip_return','trip_explain'))
    assert len({by[k]['task_id'] for k in ('trip_ask','interrupt','memo','new_question')})==4
    assert by['trip_details']['emails']==1 and by['memo']['emails']==1 and len(mail)==2
    assert all(by[k]['emails']==0 for k in ('interrupt','trip_return','trip_explain','new_question'))
    assert all(len(r['reply'].split())<=85 for r in receipts)
    print('PASS interrupted travel, unrelated draft/question, saved itinerary return; mail captured')
    sys.exit(0)
if args.scenario=='continuity-melbourne':
    by={r['case']:r for r in receipts}
    assert by['trip_ask']['completed'] and by['trip_ask']['emails']==0
    assert by['trip_details']['completed'] and by['trip_details']['emails']==1
    assert by['trip_details']['task_id']==by['trip_ask']['task_id']==by['trip_return']['task_id']
    assert by['new_question']['task_id']!=by['trip_ask']['task_id'] and by['new_question']['emails']==0
    assert by['trip_return']['emails']==0 and len(mail)==1
    assert 'Melbourne' in mail[0]['body'] and all(len(r['reply'].split())<=85 for r in receipts)
    print('PASS exact Melbourne native itinerary, auto email, unrelated question and topic return; mail captured')
    sys.exit(0)
if args.scenario.startswith('travel'):
    fragments=[r for r in receipts if r['case']=='fragment']
    assert all(r['calls']==0 and r['emails']==0 and r['reply'].endswith('?') for r in fragments)
    receipts=[r for r in receipts if r['case']!='fragment']
    assert receipts[0]['reply'].endswith('?') and receipts[0]['emails']==0
    assert receipts[1]['emails']==1 and receipts[2]['emails']==0
    assert receipts[3]['calls']==receipts[4]['calls']==0
    assert len(mail)==2 and mail[0]['body']==mail[1]['body']
    print('PASS native travel execution, explanation, saved artifact and typed resend; mail captured',flush=True)
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
