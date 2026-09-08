#!/usr/bin/env python3
"""Read-only local Qwen routing replay. No tools, mail, microphone or live state."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from openai import OpenAI
from hermes_cli.voice_task_router import route_task

client=OpenAI(base_url='http://localhost:8000/v3',api_key='local-ovms',timeout=30,max_retries=0)
agent=SimpleNamespace(client=client,base_url='http://localhost:8000/v3',model='qwen3.6-35b-a3b',_interrupt_requested=False)
pending={'id':'workshop','request':'Plan a team workshop','phase':'awaiting_details',
         'question_used':True,'facts':{},'artifact_version':None}
complete=dict(pending,phase='result_ready',artifact_version=1,
              facts={'audience':'20 marketing colleagues','duration':'one hour'})
cases=[
    ('simple','What is two plus two?',None,{'simple'}),
    ('missing_details','Please help me prepare a team workshop.',None,{'ask'}),
    ('complete_request','Draft a one-hour workshop agenda for 20 marketing colleagues to practice clear project updates. Use three activities and include facilitator notes.',None,{'execute'}),
    ('details_answer','It will be for twenty marketing colleagues, and we have one hour.',pending,{'execute'}),
    ('explanation','Why did you recommend interactive exercises for this workshop?',complete,{'simple','followup'}),
    ('new_task','Now please draft a 200-word welcome message for new employees, with a friendly tone.',complete,{'execute'}),
    ('coding','Write a Python script to parse a CSV file.',None,{'native'}),
    ('external_action','Book a flight to New York and charge my saved card.',None,{'native'}),
    ('chinese','请为二十位市场部同事安排一小时的项目汇报培训，包含三个活动和主持人提示。',None,{'execute'}),
]
receipts=[]
for name,text,active,expected in cases:
    started=time.monotonic()
    try:
        result=route_task(agent,text,active,platform='cli',modality='voice')
        passed=result['route'] in expected
        if name=='new_task':passed=passed and result['relation']=='new'
        if name=='details_answer':passed=passed and result['relation']=='answer'
        receipt={'case':name,'result':result,'seconds':round(time.monotonic()-started,2),'passed':passed}
    except Exception as exc:
        receipt={'case':name,'error':str(exc),'passed':False}
    receipts.append(receipt);print(json.dumps(receipt,ensure_ascii=False),flush=True)
folder=Path(tempfile.mkdtemp(prefix='hermes-voice-router-'))
path=folder/'receipt.json';path.write_text(json.dumps(receipts,indent=2,ensure_ascii=False))
print('RECEIPT',path,flush=True)
assert all(r['passed'] for r in receipts),'Routing acceptance failed; do not default-enable the general workflow'
print('PASS local routing cases only; not a harness/delivery or acoustic end-to-end claim',flush=True)
