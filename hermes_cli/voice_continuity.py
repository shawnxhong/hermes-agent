"""Opt-in voice continuity coordinator over native execution, not a second agent."""
import logging
import re
from hermes_cli.voice_continuity_store import ContinuityStore
from hermes_cli.voice_continuity_router import route
from hermes_cli.voice_delivery import EMAIL, StaleTask

log=logging.getLogger(__name__)
YES=re.compile(r"^(?:yes|yeah|yep|correct|是的|对|没错)(?:[\s,.!?，。！？]*(?:I mean that|please|thank you|thanks|(?:could you )?(?:just )?send (?:me )?(?:the|it|email)(?: email)?))*[\s,.!?，。！？]*$",re.I)
NO=re.compile(r'^(?:no|nope|cancel|never mind|不|不是|取消)[.!。！\s]*$',re.I)


def enabled(cfg=None):
    if cfg is None:
        from hermes_cli.general_voice import config
        cfg=config()
    from utils import is_truthy_value
    raw=cfg.get('continuity') if isinstance(cfg,dict) else None
    return isinstance(raw,dict) and is_truthy_value(raw.get('enabled'),default=False)


def _address(text,default):
    """Only literal addresses or the uniquely matching configured default repair."""
    addresses=EMAIL.findall(text)
    if addresses:return addresses[-1],False
    if EMAIL.fullmatch(default or '') and default.replace('@','.').casefold() in text.casefold():
        return default,True
    if re.search(r'\b(?:email address is|address is|(?:another|different) (?:email )?address)\b|我的邮箱|邮箱是|另一个邮箱|@',text,re.I):
        return '',True
    return default,False


def run_continuity(*,agent,user_message,session_id,input_modality,platform):
    from hermes_cli import general_voice as base
    store=ContinuityStore();session=str(session_id or '')
    if not session:return None
    cfg=base.config();pending=store.pending(session)
    if input_modality!='voice' and not pending:return None
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
    if not pending and YES.fullmatch(text):
        return finish('There is no pending confirmation. No additional email was sent.')
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
        return None
    execution_text=text
    # Content-delivery authority must occur in this utterance, not old context.
    explicit_delivery=bool(re.search(r'\b(?:e-?mail|send|resend|forward)\b|邮件|发给|发到|转发',text,re.I))
    if not explicit_delivery:
        decision['delivery']='none'
        if op=='send':op=decision['operation']='resume'
    # By contract, answer means a new question unless filling a pending slot.
    # Never let a stale topic ID turn an unrelated answer into a report revision.
    if op=='answer' and not pending and decision.get('relation')!='followup':
        decision.update(target='NEW',version=0)
    if op=='native':
        # Original harness owns external action/coding authorization, not this state.
        if pending:store.consume(session,pending['id'],status='cancelled')
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
    recipient,confirm_address=_address(execution_text,cfg.get('default_recipient',''))
    email=decision['delivery']=='email' or (not selected and task['facts'].get('initial_email',False))
    if base.NO_EMAIL.search(text):email=False
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
    if travel and decision['domain']=='travel' and op=='answer':
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
                email=email or not selected
                if base.NO_EMAIL.search(text):email=False
                reply=content['summary']
                if email:reply+=' '+delivery(task,version,recipient,confirm_address)
                return finish(reply,calls=calls)
        if result:return finish(result.get('final_response') or 'I could not finish the travel plan.',calls=calls,failed=True)
    native_route={'intent':'complex' if decision['detail'] or op in {'expand','revise'} else 'simple',
                  'relation':'new' if new else 'followup','api_calls':calls}
    def finalize(*,response_text,failed,turn_exit_reason,messages):
        nonlocal task
        count=0
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
        if not base._brief(body) or native_route['intent']=='complex':
            try:
                summary=base._summary(agent,body,task['language']);count=1
            except base.IncompleteResult:
                return finish('No complete result was produced. No email was sent.',calls=1,failed=True)
            except Exception:
                summary='The result is saved.';count=1
        sources=sorted(set(re.findall(r'https?://[^\s<>"\]\)]+',body)))
        kind='explanation' if op=='explain' else 'revision' if selected else 'main'
        task,version=store.save_result(session,task,body=body,summary=summary,kind=kind,
                                       parent_version=selected['version'] if selected else None,sources=sources,
                                       detailed=native_route['intent']=='complex')
        if email:summary+=' '+delivery(task,version,recipient,confirm_address)
        return finish(summary,calls=count)
    context={'selected_result':selected['body'][:22000] if selected else None,
             'current_operation':op,'delivery_owner':'Host. Do not ask for email or use send_message.',
             'content_only':'Return only the requested content. Email submission happens AFTER your answer, outside model tools. Do not discuss email success, failure, or unavailable delivery tools.',
             'confirmed_recipient':recipient if not confirm_address else None,
             'current_request':execution_text}
    return base.execute_native(agent,execution_text,session,store,task,native_route,cfg,delivery=finalize,extra_context=context)
