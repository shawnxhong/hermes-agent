"""Opt-in voice continuity coordinator over native execution, not a second agent."""
import logging
import re
import json
import html
from hermes_cli.voice_continuity_store import ContinuityStore
from hermes_cli.voice_continuity_router import route
from hermes_cli.voice_delivery import EMAIL, StaleTask

log=logging.getLogger(__name__)
YES=re.compile(r"^(?:yes|yeah|yep|correct|是的|对|没错)(?:[\s,.!?，。！？]*(?:I mean that|please|thank you|thanks|(?:could you )?(?:just )?send (?:me )?(?:the|it|email)(?: email)?))*[\s,.!?，。！？]*$",re.I)
NO=re.compile(r'^(?:no|nope|cancel|never mind|不|不是|取消)(?:[,，]\s*cancel)?[.!。！\s]*$',re.I)
UNRESOLVED_CHOICE=re.compile(r"\b(?:haven't|have not|hasn't|has not)\s+(?:decided|chosen|selected)\s+which\b|还没(?:决定|选好)(?:哪|要哪)",re.I)
RECIPIENT_QUESTION=re.compile(r'^(?:(?:what|which)\s+(?:is\s+(?:the|your)\s+)?(?:(?:new|email|recipient|delivery|other)\s+)*address\b|(?:could|can|would)\s+you\s+(?:provide|type|give)\s+(?:the|your)\s+(?:new\s+)?email\s+address\b)',re.I)
# Only an unambiguous transport-only change to the currently selected result.
# Named old topics, edits, expansions and compound content requests still route.
CURRENT_REDIRECT=re.compile(
    r'^(?:please\s+)?(?:(?:could|can|would)\s+you\s+)?(?:also\s+|just\s+)?'
    r'(?:send|resend|forward|email)\s+(?:me\s+)?'
    r'(?:it|this|that|the\s+(?:same\s+)?(?:report|email|result|answer|details|draft|document|plan|list|itinerary))'
    r'\s+to\s+(?:(?:a|an|the)\s+)?(?:another|different|new)\s+(?:email\s+)?address'
    r'[.!?\s]*(?:I\s+(?:will|can)\s+type\s+(?:it|(?:that|the)\s+email\s+address)(?:\s+to\s+you)?[.!?\s]*)?$',re.I)


def enabled(cfg=None):
    if cfg is None:
        from hermes_cli.general_voice import config
        cfg=config()
    from utils import is_truthy_value
    raw=cfg.get('continuity') if isinstance(cfg,dict) else None
    return isinstance(raw,dict) and is_truthy_value(raw.get('enabled'),default=False)


def source_notes(payloads,allowed):
    """Bounded extractive evidence, never model-authored source descriptions."""
    notes={}
    def walk(value):
        if isinstance(value,dict):
            if value.get('error') or value.get('success') is False:return
            url=value.get('url')
            snippet=value.get('description') or value.get('content') or value.get('text')
            if isinstance(url,str) and url in allowed and isinstance(snippet,str) and snippet.strip():
                clean=html.unescape(re.sub(r'<[^>]+>','',snippet)).strip()
                notes[url]={'url':url,'title':str(value.get('title') or url)[:200],'excerpt':clean[:1400]}
            for item in value.values():walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    for payload in payloads:walk(payload)
    return list(notes.values())[-8:]


def evidence_messages(messages):
    for item in messages:
        if item.get('role')!='tool' or item.get('name') not in {'web_search','web_extract'}:continue
        content=item.get('content')
        if not isinstance(content,str):continue
        start=content.find('{')
        if start<0:continue
        try:yield json.JSONDecoder().raw_decode(content[start:])[0]
        except ValueError:continue


def _address(text,default,*,delivery_requested=False):
    """Only literal addresses or the uniquely matching configured default repair."""
    directive=bool(re.search(r'(?:^|[,;.!?]\s+|\band\s+)(?:(?:please|(?:could|can|would) you|I want you to)\s+)*(?:also\s+|just\s+)?(?:send|resend|forward|deliver|email|mail)\b',text,re.I))
    stated=bool(re.search(r'^(?:my (?:email )?address is|我的邮箱(?:是)?|邮箱是)\s*',text,re.I))
    # An address being discussed is task data, not a destination override.
    # Auto-email of an explanation must still go to the configured recipient.
    if not stated and not (delivery_requested and directive):return default,False
    addresses=EMAIL.findall(text) if stated else re.findall(r'\b(?:to|at)\s+('+EMAIL.pattern+r')',text,re.I)
    if len(addresses)>1:return '',True
    if addresses:return addresses[0],False
    if EMAIL.fullmatch(default or '') and default.replace('@','.').casefold() in text.casefold():
        return default,True
    if re.search(r'\b(?:email address is|address is|(?:another|different|new) (?:email )?address)\b|我的邮箱|邮箱是|另一个邮箱|@',text,re.I):
        return '',True
    return default,False


def run_continuity(*,agent,user_message,session_id,input_modality,platform):
    from hermes_cli import general_voice as base
    store=ContinuityStore();session=str(session_id or '')
    if not session:return None
    cfg=base.config();pending=store.pending(session)
    if input_modality!='voice' and not pending:
        current=store.current(session)
        if current:store.close(session,current)
        return None
    text=user_message.strip()
    task=store.current(session)
    def finish(reply,*,calls=0,failed=False):
        store.record_turn(session,text,reply)
        return base._handled(reply,calls=calls,failed=failed)
    def send_selected(task,version,recipient):
        status=store.submit_result(session,task,version,recipient,sender=base._send,
                                   interrupted=lambda:agent._interrupt_requested)
        return base._status(status,task['language']=='zh')
    def delivery(task,version,recipient,needs_confirmation):
        if needs_confirmation or not EMAIL.fullmatch(recipient or ''):
            question=(f'Did you mean {recipient}?' if recipient else 'Which email address should receive these details?')
            store.pend(session,task,'confirm_recipient' if recipient else 'recipient',
                       {'version':version,'recipient':recipient,'question':question})
            return question
        return send_selected(task,version,recipient)
    # Only a scoped pending content interaction can interpret yes/no without a model.
    if pending and (NO.fullmatch(text) or (pending['kind'] in {'recipient','confirm_recipient'} and base.NO_EMAIL.search(text))):
        store.consume(session,pending['id'],status='cancelled')
        return finish('Cancelled. No additional email was sent.')
    if pending and pending['kind']=='confirm_recipient' and YES.fullmatch(text):
        job=store.consume(session,pending['id'])
        return finish(send_selected(task,job['version'],job['recipient']))
    if pending and pending['kind'] in {'recipient','confirm_recipient'} and EMAIL.fullmatch(text):
        job=store.consume(session,pending['id'])
        return finish(send_selected(task,job['version'],text))
    if not pending and not task and (YES.fullmatch(text) or NO.fullmatch(text)):
        return None  # A native-harness question/approval is not our confirmation.
    if not pending and YES.fullmatch(text):
        return finish('There is no pending confirmation. No additional email was sent.')
    if not pending and NO.fullmatch(text):
        return finish('There is no pending delivery. No additional email was sent.')
    if (not pending and task and UNRESOLVED_CHOICE.search(text)
            and re.search(r'\b(?:email|send|forward)\b|邮件|发给',text,re.I)
            and not base.NO_EMAIL.search(text)
            and len([item for item in store.topics(session) if item['version']])>=2):
        question='Which saved result would you like me to email?'
        store.pend(session,task,'reference',{'question':question,'request':text,
                   'decision':{'operation':'send','delivery':'email','detail':False}})
        return finish(question)
    if not pending and task and CURRENT_REDIRECT.fullmatch(text):
        selected=store.result(session,task['id'],task.get('artifact_version'))
        if selected:
            return finish(delivery(task,selected['version'],'',True))
    if pending and len(text.split())<12 and re.search(r'(?:\.{3}|…)\s*$',text):
        paused=store.unclear(session,pending['id'])
        return finish('I have paused this step. You can return to it later.' if paused else 'I did not catch that. Could you say your answer again?')
    try:
        decision=dict(route(agent,text,store,session,pending))
    except Exception:
        log.exception('Continuity routing failed without changing content')
        return finish('I could not interpret that safely. Your previous results are still saved.',failed=True)
    op=decision['operation'];calls=decision['api_calls']
    if input_modality!='voice' and decision['target']=='NEW':
        if pending:store.consume(session,pending['id'],status='cancelled')
        if task:store.close(session,task)
        return None
    execution_text=text
    # Content-delivery authority must occur in this utterance, not old context.
    explicit_delivery=bool(re.search(r'\b(?:e-?mail|send|resend|forward)\b|邮件|发给|发到|转发',text,re.I))
    if not explicit_delivery:
        decision['delivery']='none'
        if op=='send':op=decision['operation']='resume'
    # A named-content ambiguity is a resolvable delivery question, not an ASR
    # failure. Preserve its explicit delivery request while asking for the topic.
    if op=='unclear' and decision['question'] and decision['target']!='NEW' and decision['delivery']=='email' and not pending:
        op=decision['operation']='send'
    # By contract, answer means a new question unless filling a pending slot.
    # Never let a stale topic ID turn an unrelated answer into a report revision.
    if op=='answer' and not pending and decision.get('relation')!='followup':
        decision.update(target='NEW',version=0)
    if op=='native':
        # Original harness owns external action/coding authorization, not this state.
        if pending:store.consume(session,pending['id'],status='cancelled')
        if task:store.close(session,task)  # Keep saved topics, release current-turn ownership.
        return None
    if op in {'cancel','deny'}:
        if pending:store.consume(session,pending['id'],status='cancelled')
        return finish('Cancelled.',calls=calls)
    if op in {'unclear','confirm'}:
        if pending and op=='confirm' and pending['kind']=='confirm_recipient':
            job=store.consume(session,pending['id'])
            return finish(send_selected(task,job['version'],job['recipient']),calls=calls)
        paused=store.unclear(session,pending['id']) if pending else True
        return finish('I have paused this step; your previous results are saved.' if paused else
                      'I did not understand your answer. Could you say it again?',calls=calls)
    new=decision['target']=='NEW'
    if pending and pending['kind']=='reference' and not new:
        original=store.consume(session,pending['id'])
        execution_text=original['request']
        for key in ('operation','delivery','detail'):
            decision[key]=original['decision'][key]
        decision['question']=''
        op=decision['operation']
        pending=None
    if new:
        task=store.start(session,text,language=decision['language'])
        task=store.supply_details(session,task,{'initial_email':(decision['detail'] or decision['domain']=='travel') and not bool(base.NO_EMAIL.search(text))})
    else:
        task=store.select(session,decision['target'])
    selected=store.result(session,task['id'],decision['version'] or task.get('artifact_version'))
    if pending and not new and pending['task_id']==task['id']:
        if pending['kind']=='requirements':
            store.consume(session,pending['id'])
            task=store.supply_details(session,task,{'confirmed_details':text})
        elif pending['kind']=='reference':
            store.consume(session,pending['id'])
        elif pending['kind'] in {'recipient','confirm_recipient'}:
            if not decision['version']:
                selected=store.result(session,task['id'],pending['payload']['version'])
            store.consume(session,pending['id'],status='cancelled')
    recipient,confirm_address=_address(execution_text,cfg.get('default_recipient',''),delivery_requested=decision['delivery']=='email')
    email=decision['delivery']=='email' or (not selected and task['facts'].get('initial_email',False))
    if base.NO_EMAIL.search(text):email=False
    if op=='send' and selected and confirm_address and (RECIPIENT_QUESTION.search(decision['question']) or decision['question'].strip() in {recipient,recipient.replace('@','.')}):
        decision['question']=''  # The host's recipient slot, never a content-reference slot.
    if op=='send' and not selected:
        return finish('There is no completed saved result for that task. No email was sent.',calls=calls,failed=True)
    # A brief saved answer cannot satisfy a request for a detailed deliverable.
    if op=='send' and selected and decision['detail'] and not selected['detailed']:
        op=decision['operation']='expand'
    if op=='send' and selected and not decision['question']:
        return finish(delivery(task,selected['version'],recipient,confirm_address),calls=calls)
    if op=='resume' and selected and not decision['question']:
        return finish(selected['summary'],calls=calls)
    question=decision['question']
    if question and new and decision['detail'] and decision['domain']!='travel':
        task=store.ask_once(session,task,question)
        store.pend(session,task,'requirements',{'question':question})
        return finish(question,calls=calls)
    if question and not new:
        store.pend(session,task,'reference',{'question':question,'request':text,'decision':decision})
        return finish(question,calls=calls)
    # Strategy produces content only. The same host records and delivers it.
    travel=base.travel_plugin()
    if travel and decision['domain']=='travel' and (op=='answer' or (not selected and op in {'expand','revise'})):
        result=travel.run_workflow(agent=agent,user_message=text,session_id=task['id'],
            input_modality='voice',platform=platform,managed_delivery=True)
        if result and not result.get('failed'):
            content=result.get('continuity_result') or {}
            calls+=result.get('api_calls',0)
            if content.get('awaiting')=='facts':
                if task['question_used']:
                    return finish('I still lack enough information to complete the plan. Your earlier results are saved.',calls=calls,failed=True)
                task=store.ask_once(session,task,result['final_response'])
                store.pend(session,task,'requirements',{'question':result['final_response']})
                return finish(result['final_response'],calls=calls)
            if content.get('body'):
                task,version=store.save_result(session,task,body=content['body'],summary=content['summary'],
                                               kind='revision' if selected else 'main',detailed=True)
                # Completing an already requested initial plan retains initial email policy.
                if base.NO_EMAIL.search(text):email=False
                reply=content['summary']
                if email:reply+=' '+delivery(task,version,recipient,confirm_address)
                return finish(reply,calls=calls)
        if result:return finish(result.get('final_response') or 'I could not finish the travel plan.',calls=calls,failed=True)
    native_route={'intent':'complex' if decision['detail'] or op in {'expand','revise'} else 'simple',
                  'relation':'new' if new else 'followup','api_calls':calls}
    retrieved_sources=set()
    retrieved_payloads=[]
    retrieval_failed=False
    def observe_tool(name,args,data):
        nonlocal retrieval_failed
        if name in {'web_search','web_extract'}:
            if isinstance(data,dict) and (data.get('error') or data.get('success') is False):
                retrieval_failed=True
                return
            retrieved_sources.update(re.findall(r'https?://[^\s<>"\]\)]+',json.dumps(data,ensure_ascii=False)))
            retrieved_payloads.append(data)
    def finalize(*,response_text,failed,turn_exit_reason,messages):
        nonlocal task
        count=0
        if native_route['intent']=='complex' and retrieval_failed and not retrieved_sources and not (selected or {}).get('sources'):
            return finish('I could not verify the requested information because search failed. No new email was sent.',failed=True)
        if failed or turn_exit_reason!='text_response(finish_reason=stop)' or not response_text.strip():
            return finish('I could not finish this step. Your earlier results are saved; no new email was sent.',failed=True)
        body=base._strip_delivery_claims(response_text)
        if not body or re.fullmatch(r'\s*(NO_REPLY_EXPECTED|NO_REPLY|SILENT_REPLY)\s*',body):
            return finish('No complete result was produced. No email was sent.',failed=True)
        if body.endswith(('?','？')) and base._brief(body):
            if task['question_used']:
                return finish('I still lack enough information to complete this step. Your earlier results are saved.',failed=True)
            task=store.ask_once(session,task,body)
            store.pend(session,task,'requirements',{'question':body})
            return finish(body)
        summary=body
        sources=sorted(retrieved_sources | set((selected or {}).get('sources',[])))
        evidence=source_notes([*evidence_messages(messages),*retrieved_payloads],set(sources))
        if decision.get('execution')=='research' and not sources:
            return finish('I could not verify this information from retrieved sources. No new email was sent.',failed=True)
        # A short numbered answer is already content, not an incomplete report.
        # Removing sequential list markers avoids an unnecessary model validator
        # (which can falsely reject a perfectly valid names-only answer).
        markers=list(re.finditer(r'(?:^|\s)([1-9]\d*)[.)]\s+',body))
        if markers and markers[0].start()==0 and [int(m[1]) for m in markers]==list(range(1,len(markers)+1)):
            summary='; '.join(body[m.end():markers[i+1].start() if i+1<len(markers) else len(body)].strip() for i,m in enumerate(markers))
        if not base._brief(summary) or native_route['intent']=='complex':
            try:
                cited={url.rstrip('.,;') for url in re.findall(r'https?://[^\s<>"\]\)]+',body)}
                if sources and (not cited or not cited<={url.rstrip('.,;') for url in sources}):
                    raise base.UngroundedResult('Missing or unobserved source citations')
                count=1
                summary=base._summary(agent,body,task['language'],evidence if sources else None,task['request']+'\nCurrent request: '+execution_text)
            except Exception as error:
                if agent._interrupt_requested:
                    return finish('Cancelled. No new email was sent.',calls=count,failed=True)
                if evidence:
                    # Validator rejection or outage cannot justify mailing the
                    # unchecked synthesis. Source excerpts remain useful data.
                    body='Source notes for your request (not a verified synthesized report). Some generated details could not be verified, so only retrieved excerpts are included below. Source information may be outdated; confirm current details directly.\n\n'+'\n\n'.join(item['title']+'\n'+item['url']+'\nSource excerpt: '+item['excerpt'] for item in evidence)
                    summary='Some details could not be verified. I have prepared the available source notes instead.'
                elif isinstance(error,(base.IncompleteResult,base.UngroundedResult)):
                    return finish('No complete verified result was produced. No email was sent.',calls=count,failed=True)
                elif sources:
                    return finish('I could not complete source verification. No new email was sent.',calls=1,failed=True)
                else:
                    summary='The result is saved.';count=1
        # Provenance comes from successful tools in THIS turn, never model-written
        # URLs or unrelated historical tool messages. A revision may combine
        # its parent's evidence with newly retrieved evidence for the same task.
        sources=sorted(retrieved_sources | set((selected or {}).get('sources',[])))
        kind='explanation' if op=='explain' else 'revision' if selected else 'main'
        task,version=store.save_result(session,task,body=body,summary=summary,kind=kind,
                                       parent_version=selected['version'] if selected else None,sources=sources,
                                       detailed=native_route['intent']=='complex')
        if email:summary+=' '+delivery(task,version,recipient,confirm_address)
        return finish(summary,calls=count)
    context={'selected_result':selected['body'][:22000] if selected else None,
             'requires_retrieval':decision.get('execution')=='research',
             'retrieval_instruction':'This is a research request. Use web_search for the actual current question before answering. Do not answer external recommendations or new factual details from memory or from the prior generated answer.' if decision.get('execution')=='research' else '',
             'prior_retrieved_sources':selected.get('sources',[]) if selected else [],
             'research_policy':'For external recommendations or factual detail, attribute each concrete claim to a retrieved source about that exact entity. A search snippet about one entity does not verify another. Never transfer locations, specialties, prices or hours between entries. When expanding a short recommendation, search or fetch the named entities to verify NEW specifics; the prior generated answer is not evidence. Include supporting source links in the detailed text. If a specific detail is not supported, omit it or explicitly mark it unverified. Do not invent details to fill a template. This does not require research for self-contained drafting, arithmetic or stable conceptual explanations.' if native_route['intent']=='complex' else '',
             'current_operation':op,'delivery_owner':'Host. Do not ask for email or use send_message.',
             'brief_research_scope':'For a names-only request, return names only. One search is sufficient when it returns the requested names; do not research each entity until details are requested. Do not narrate tool budgets or your summarization process.',
             'content_only':'Return only the requested content. Email submission happens AFTER your answer, outside model tools. Do not discuss email success, failure, or unavailable delivery tools.',
             'confirmed_recipient':recipient if not confirm_address else None,
             'current_request':execution_text}
    return base.execute_native(agent,execution_text,session,store,task,native_route,cfg,delivery=finalize,extra_context=context,observe_tool=observe_tool)
