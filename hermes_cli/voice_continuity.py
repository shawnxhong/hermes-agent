"""Opt-in voice continuity coordinator over native execution, not a second agent."""
import logging
import re
import json
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
        if pending:
            return finish('I did not understand that answer. Could you say it again?')
        return None  # Native Hermes can still answer; continuity is an enhancement, not a gate.
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
        task=store.supply_details(session,task,{
            'initial_email':decision['detail'] and not bool(base.NO_EMAIL.search(text)),
            'suppress_auto_email':bool(base.NO_EMAIL.search(text)),
        })
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
    email=(decision['delivery']=='email' or
           (not task['facts'].get('suppress_auto_email',False)
            and (task['facts'].get('initial_email',False) or decision['detail'])
            and (not selected or not selected.get('detailed',False))))
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
    if question and not new:
        store.pend(session,task,'reference',{'question':question,'request':text,'decision':decision})
        return finish(question,calls=calls)
    native_route={'intent':'complex' if decision['detail'] or op in {'expand','revise'} else 'simple',
                  'relation':'new' if new else 'followup','api_calls':calls}
    retrieved_sources=set()
    retrieval_failed=False
    retrieval_attempted=False
    retrieval_succeeded=False
    def observe_tool(name,args,data):
        nonlocal retrieval_attempted,retrieval_failed,retrieval_succeeded
        if name in {'web_search','web_extract'}:
            retrieval_attempted=True
            if isinstance(data,dict) and (data.get('error') or data.get('success') is False):
                retrieval_failed=True
                return
            retrieval_succeeded=True
            retrieved_sources.update(re.findall(r'https?://[^\s<>"\]\)]+',json.dumps(args,ensure_ascii=False)))
            retrieved_sources.update(re.findall(r'https?://[^\s<>"\]\)]+',json.dumps(data,ensure_ascii=False)))
    def finalize(*,response_text,failed,turn_exit_reason,messages):
        nonlocal task
        count=0
        if (native_route['intent']=='simple' and response_text.strip()
                and turn_exit_reason in {'workflow_incomplete_output','text_response(finish_reason=length)'}):
            try:
                response_text=base._summary(agent,response_text,task['language'],None,execution_text);count=1
            except Exception as error:
                log.warning('Truncated simple answer summarization failed; using bounded fallback: %s',error)
                response_text=base._fallback_summary(response_text,task['language']);count=1
            failed=False;turn_exit_reason='text_response(finish_reason=stop)'
        if failed or turn_exit_reason!='text_response(finish_reason=stop)' or not response_text.strip():
            recovered=''
            if retrieval_succeeded and not agent._interrupt_requested:
                try:
                    recovered=base._recover_tool_result(agent,execution_text,messages,task['language'])
                    count=1 if recovered else 0
                except Exception as error:
                    log.warning('Read-only tool-loop recovery failed: %s',error)
            if not recovered:
                return finish('I could not finish this step. Your earlier results are saved; no new email was sent.',failed=True)
            response_text=recovered
        body=base._strip_delivery_claims(response_text)
        if not body or re.fullmatch(r'\s*(NO_REPLY_EXPECTED|NO_REPLY|SILENT_REPLY)\s*',body):
            return finish('No complete result was produced. No email was sent.',failed=True)
        if body.endswith(('?','？')) and base._brief(body):
            previous=task.get('last_question','')
            normalize=lambda value:re.sub(r'\W','',value).casefold()
            if previous and normalize(previous)==normalize(body):
                return finish('I have your answer, but I could not complete this step. You can continue with a different request.',failed=True)
            task=store.await_details(session,task,body)
            store.pend(session,task,'requirements',{'question':body})
            return finish(body)
        sources=sorted(retrieved_sources | set((selected or {}).get('sources',[])))
        allowed={url.rstrip('.,;') for url in sources}
        for url in set(re.findall(r'https?://[^\s<>"\]\)]+',body)):
            if url.rstrip('.,;') not in allowed:
                body=body.replace(url,'[unverified link omitted]')
        detailed=native_route['intent']=='complex'
        if detailed:
            if sources:
                missing=[url for url in sources if url not in body]
                if missing:
                    body+='\n\nSources retrieved for reference (not claim-by-claim verification):\n'+'\n'.join(missing)
                if retrieval_failed:
                    body+='\n\nVerification note: Some requested source lookups were unavailable; confirm time-sensitive details.'
            elif decision.get('execution')=='research':
                body+='\n\nVerification note: Live sources were unavailable or not used; confirm current and time-sensitive details.'
        summary=body
        # A short numbered answer is already content, not an incomplete report.
        # Removing sequential list markers avoids an unnecessary model validator
        # (which can falsely reject a perfectly valid names-only answer).
        markers=list(re.finditer(r'(?:^|\s)([1-9]\d*)[.)]\s+',body))
        if markers and markers[0].start()==0 and [int(m[1]) for m in markers]==list(range(1,len(markers)+1)):
            summary='; '.join(body[m.end():markers[i+1].start() if i+1<len(markers) else len(body)].strip() for i,m in enumerate(markers))
        retrieval_unavailable=retrieval_attempted and not retrieval_succeeded
        if retrieval_unavailable:
            summary=('无法获取实时来源，因此无法核验所请求的当前信息。' if task['language']=='zh' else
                     'I could not retrieve live sources, so I could not verify the requested current details.')
        elif not base._brief(summary) or native_route['intent']=='complex':
            try:
                count=1
                summary=base._summary(agent,body,task['language'],
                                      {'retrieved_sources':sources,'retrieval_incomplete':retrieval_failed},
                                      task['request']+'\nCurrent request: '+execution_text)
            except Exception as error:
                if agent._interrupt_requested:
                    return finish('Cancelled. No new email was sent.',calls=count,failed=True)
                log.warning('Voice summarization failed; retaining native result: %s',error)
                summary=base._fallback_summary(body,task['language']);count=1
        # Provenance comes from successful tools in THIS turn, never model-written
        # URLs or unrelated historical tool messages. A revision may combine
        # its parent's evidence with newly retrieved evidence for the same task.
        sources=sorted(retrieved_sources | set((selected or {}).get('sources',[])))
        kind='explanation' if op=='explain' else 'revision' if selected else 'main'
        task,version=store.save_result(session,task,body=body,summary=summary,kind=kind,
                                       parent_version=selected['version'] if selected else None,sources=sources,
                                       detailed=detailed)
        if email and retrieval_unavailable:
            summary+=' No automatic email was sent because no live source could be retrieved.'
        elif email:
            summary+=' '+delivery(task,version,recipient,confirm_address)
        return finish(summary,calls=count)
    context={'selected_result':selected['body'][:22000] if selected else None,
             'retrieval_guidance':(('This is open-ended general guidance, not a live-fact request. '
                                    'Answer from stable knowledge without tools and offer to check current details if useful. '
                                    if native_route['intent']=='simple' and base.OPEN_ENDED.search(execution_text)
                                    and not base.EXPLICIT_DELIVERABLE.search(execution_text) else
                                    'For this brief answer, use at most one search and no repeated extraction. '
                                    if native_route['intent']=='simple' else
                                    'Use available retrieval when it materially improves current or external facts. ')
                                   +'If retrieval is unavailable, give the useful answer you can and label uncertainty.'
                                   if decision.get('execution')=='research' else ''),
             'prior_retrieved_sources':selected.get('sources',[]) if selected else [],
             'current_operation':op,'delivery_owner':'Host. Do not ask for email or use send_message.',
             'content_only':'Return only the requested content. Email submission happens AFTER your answer, outside model tools. Do not discuss email success, failure, or unavailable delivery tools.',
             'confirmed_recipient':recipient if not confirm_address else None,
             'current_request':execution_text}
    return base.execute_native(agent,execution_text,session,store,task,native_route,cfg,delivery=finalize,extra_context=context,observe_tool=observe_tool)
