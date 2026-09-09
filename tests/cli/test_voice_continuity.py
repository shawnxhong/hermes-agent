from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from hermes_cli import general_voice as base, voice_continuity as voice
from hermes_cli.voice_continuity_store import ContinuityStore


def decision(**kw):
    return dict({'target':'NEW','operation':'answer','detail':False,'delivery':'none','version':0,
                 'question':'','summary':'Restaurant recommendations','language':'en','domain':'general','api_calls':1},**kw)


@pytest.fixture
def rig(monkeypatch):
    cfg={'enabled':True,'continuity':{'enabled':True},'default_recipient':'xiaoheng.hong@intel.com'}
    monkeypatch.setattr(base,'config',lambda:cfg)
    router=Mock(return_value=decision());monkeypatch.setattr(voice,'route',router)
    sender=Mock(return_value={'success':True});monkeypatch.setattr(base,'_send',sender)
    monkeypatch.setattr(base,'_summary',lambda *args:'A useful summary.')
    agent=SimpleNamespace(session_id='s',base_url='http://localhost:8000/v3',_interrupt_requested=False)
    return agent,router,sender


def run(rig,text='Name some restaurants.',modality='voice'):
    return base.run_workflow(agent=rig[0],user_message=text,session_id='s',input_modality=modality,platform='cli')


def done(value,body='Wangbijib, Maple Tree, Saemaul and Yang Good.'):
    return value['continuation'].finalize(response_text=body,failed=False,turn_exit_reason='text_response(finish_reason=stop)',messages=[])


def test_short_answer_then_details_by_default_email(rig):
    agent,router,sender=rig
    done(run(rig));store=ContinuityStore();task=store.current('s')
    assert store.result('s',task['id']) and sender.call_count==0
    router.return_value=decision(target=task['id'],operation='expand',detail=True,delivery='email')
    policy=run(rig,'Give me details of those restaurants by email.')
    assert 'Wangbijib' in policy['continuation'].context
    done(policy,'Detailed information about the original four restaurants.')
    sender.assert_called_once_with('xiaoheng.hong@intel.com','Detailed information about the original four restaurants.')


def test_short_numbered_answer_needs_no_fallible_summary_call(rig,monkeypatch):
    summary=Mock(side_effect=AssertionError('Do not reclassify a short list'))
    monkeypatch.setattr(base,'_summary',summary)
    reply=done(run(rig),'1. Maple Tree 2. Wangbijip 3. Yang Good 4. Saemaul')
    assert reply['final_response']=='Maple Tree; Wangbijip; Yang Good; Saemaul'
    summary.assert_not_called();rig[2].assert_not_called()


def test_address_inside_task_content_is_not_an_automatic_recipient_override(rig):
    rig[1].return_value=decision(detail=True)
    done(run(rig,'Explain why email sent to somebody@example.org might bounce.'),'A detailed explanation of mail delivery errors.')
    assert rig[2].call_args.args[0]=='xiaoheng.hong@intel.com'


def test_explicit_report_delivery_clause_can_override_recipient(rig):
    rig[1].return_value=decision(detail=True,delivery='email')
    done(run(rig,'Draft a welcome memo and email it to other@example.org.'),'A complete welcome memo.')
    assert rig[2].call_args.args[0]=='other@example.org'


def test_named_report_new_address_does_not_fall_back_to_default(rig):
    done(run(rig));task=ContinuityStore().current('s')
    rig[1].return_value=decision(target=task['id'],operation='send',delivery='email')
    assert run(rig,'Send the restaurant list to a new email address.')['final_response'].endswith('?')
    assert ContinuityStore().pending('s')['kind']=='recipient'
    rig[2].assert_not_called()


def test_requested_details_cannot_email_only_saved_brief_answer(rig):
    _,router,sender=rig
    done(run(rig));store=ContinuityStore();task=store.current('s')
    assert not store.result('s',task['id'])['detailed']
    router.return_value=decision(target=task['id'],operation='send',detail=True,delivery='email')
    value=run(rig,'Email me detailed information about those restaurants.')
    sender.assert_not_called()
    done(value,'The expanded restaurant report.')
    assert store.result('s',task['id'])['detailed']
    sender.assert_called_once_with('xiaoheng.hong@intel.com','The expanded restaurant report.')
    before=sender.call_count
    # A repeated request reuses the detailed artifact and its delivery receipt.
    assert 'continuation' not in run(rig,'Email me those details again.')
    assert sender.call_count==before


def test_invalid_address_confirmation_yes_is_zero_model_and_once(rig):
    _,router,sender=rig
    done(run(rig));task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],operation='send',delivery='email')
    reply=run(rig,'Send it to xiaoheng.hong.intel.com.')
    assert reply['final_response']=='Did you mean xiaoheng.hong@intel.com?'
    before=router.call_count
    assert 'submitted' in run(rig,'Yes, I mean that. Could you just send me the email?')['final_response']
    run(rig,'Yes, I mean that. Just send me the email.')
    assert router.call_count==before and sender.call_count==1


def test_override_is_one_delivery_and_typed_pending_address(rig):
    _,router,sender=rig
    done(run(rig));task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],operation='send',delivery='email')
    assert run(rig,'Send it to another email address.')['final_response'].endswith('?')
    run(rig,'other@example.com','text')
    assert sender.call_args.args[0]=='other@example.com'
    run(rig,'Email those recommendations to me.')
    assert sender.call_args.args[0]=='xiaoheng.hong@intel.com'


@pytest.mark.parametrize('utterance',[
    'Please send the report to another email address.',
    'Could you also send the email to a different email address? I will type that email address to you.',
    'Send it to another email address.',
])
def test_current_recipient_only_change_cannot_become_a_new_content_task(rig,utterance):
    _,router,sender=rig
    done(run(rig));task=ContinuityStore().current('s');calls=router.call_count
    router.side_effect=AssertionError('No model should reinterpret a transport-only change')
    assert run(rig,utterance)['final_response'].endswith('?')
    run(rig,'other@example.com','text')
    assert ContinuityStore().current('s')['id']==task['id']
    assert router.call_count==calls
    sender.assert_called_once()


def test_first_detailed_expansion_auto_emails_and_explanation_keeps_main(rig):
    _,router,sender=rig
    done(run(rig));store=ContinuityStore();task=store.current('s')
    router.return_value=decision(target=task['id'],operation='expand',detail=True)
    done(run(rig,'More detail please.'),'Expanded content.')
    main=store.current('s')['artifact_version']
    router.return_value=decision(target=task['id'],operation='explain')
    done(run(rig,'Why those choices?'),'Because they suit your request.')
    assert store.current('s')['artifact_version']==main
    sender.assert_called_once_with('xiaoheng.hong@intel.com','Expanded content.')


def test_return_to_previous_topic_does_not_substitute_latest_topic(rig):
    _,router,sender=rig
    done(run(rig));store=ContinuityStore();old=store.current('s')
    done(run(rig,'What does RSVP mean?'),'Please respond.')
    router.return_value=decision(target=old['id'],operation='send',delivery='email')
    run(rig,'Email the earlier restaurant list.')
    assert sender.call_args.args[1].startswith('Wangbijib')


def test_new_task_invalidates_old_yes(rig):
    _,router,sender=rig
    done(run(rig));store=ContinuityStore();old=store.current('s')
    router.return_value=decision(target=old['id'],operation='send',delivery='email')
    run(rig,'Email it to xiaoheng.hong.intel.com.')
    router.return_value=decision()
    done(run(rig,'What does RSVP mean?'),'Please respond.')
    run(rig,'Yes')
    sender.assert_not_called()


def test_requirements_answer_keeps_initial_complex_delivery(rig):
    _,router,sender=rig
    router.return_value=decision(detail=True,question='Who is the audience?')
    run(rig,'Plan a meeting.')
    task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],detail=True)
    done(run(rig,'Twenty colleagues for one hour.'),'A complete meeting agenda.')
    sender.assert_called_once()


@pytest.mark.parametrize('operation',['send','unclear'])
def test_ambiguous_reference_retains_original_send_request(rig,operation):
    _,router,sender=rig
    done(run(rig));store=ContinuityStore();task=store.current('s')
    router.return_value=decision(target=task['id'],operation=operation,delivery='email',question='Do you mean the restaurants?')
    # Reference question must take precedence over sending.
    reply=run(rig,'Send the earlier thing.')
    assert reply['final_response'].endswith('?')
    router.return_value=decision(target=task['id'],operation='answer')
    run(rig,'The restaurant list.')
    sender.assert_called_once()


def test_native_action_keeps_original_harness(rig):
    rig[1].return_value=decision(operation='native')
    assert run(rig,'Delete a file.') is None
    rig[2].assert_not_called()


def test_native_handoff_keeps_its_yes_no_replies_and_preserves_old_topics(rig):
    done(run(rig));store=ContinuityStore();old=store.current('s')
    rig[1].return_value=decision(operation='native')
    assert run(rig,'Write a Python function.') is None
    assert store.current('s') is None and store.result('s',old['id'])
    before=rig[1].call_count
    assert run(rig,'Yes') is None and run(rig,'No, cancel.') is None
    assert rig[1].call_count==before


def test_unprompted_keyboard_handoff_does_not_steal_later_native_confirmation(rig):
    done(run(rig));store=ContinuityStore();old=store.current('s')
    assert run(rig,'A new keyboard task.','text') is None
    assert run(rig,'Yes') is None
    assert store.current('s') is None and store.result('s',old['id'])


def test_old_topic_and_email_intent_cannot_capture_new_self_contained_answer(rig):
    _,router,sender=rig
    done(run(rig));old=ContinuityStore().current('s')
    router.return_value=decision(target=old['id'],operation='answer',delivery='email')
    done(run(rig,'What does RSVP mean?'),'Please respond.')
    assert ContinuityStore().current('s')['id']!=old['id']
    sender.assert_not_called()


def test_disabled_im_and_unprompted_keyboard_do_not_route(rig):
    assert run(rig,'A normal typed request.','text') is None
    assert base.run_workflow(agent=rig[0],user_message='Hello',session_id='s',input_modality='voice',platform='feishu') is None
    rig[1].assert_not_called()


def test_semantic_denial_cancels_candidate_without_sending(rig):
    _,router,sender=rig
    done(run(rig));task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],operation='send',delivery='email')
    run(rig,'Send it to xiaoheng.hong.intel.com.')
    router.return_value=decision(target=task['id'],operation='deny')
    assert run(rig,'That is not the address I meant.')['final_response']=='Cancelled.'
    assert ContinuityStore().pending('s') is None
    run(rig,'Yes')
    sender.assert_not_called()


def test_typed_answer_to_voice_requirements_continues_but_new_task_passes_through(rig):
    _,router,sender=rig
    router.return_value=decision(detail=True,question='Who is the audience?')
    done(run(rig,'Draft a meeting agenda.'),'Who is the audience?')
    task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],detail=True)
    done(run(rig,'Twenty colleagues.','text'),'Agenda for twenty colleagues.')
    sender.assert_called_once()
    router.return_value=decision(target=task['id'],operation='send',delivery='email')
    run(rig,'Send it to another email address.')
    router.return_value=decision()
    assert run(rig,'Explain Python lists.','text') is None
    assert ContinuityStore().pending('s') is None


def test_return_to_interrupted_requirements_does_not_start_duplicate_topic(rig):
    _,router,sender=rig
    router.return_value=decision(detail=True,question='Who is the audience?')
    run(rig,'Prepare a workshop.')
    task=ContinuityStore().current('s')
    router.return_value=decision()
    done(run(rig,'What does RSVP mean?'),'Please respond.')
    router.return_value=decision(target=task['id'],relation='followup',detail=True)
    value=run(rig,'Back to the workshop: twenty colleagues for one hour.')
    done(value,'A one-hour workshop for twenty colleagues.')
    assert ContinuityStore().current('s')['id']==task['id']
    sender.assert_called_once()


def test_interrupted_initial_trip_retains_first_result_delivery_intent(rig):
    _,router,sender=rig
    router.return_value=decision(domain='travel',detail=False)
    done(run(rig,'I want to visit New York from Vancouver.'),'Which month and how many days?')
    task=ContinuityStore().current('s')
    router.return_value=decision()
    done(run(rig,'How many minutes are in two hours?'),'120 minutes.')
    router.return_value=decision(target=task['id'],relation='followup',operation='revise',detail=True,domain='travel')
    done(run(rig,'Back to New York: four days in December.'),'A four-day New York itinerary.')
    sender.assert_called_once_with('xiaoheng.hong@intel.com','A four-day New York itinerary.')


def test_exact_melbourne_followup_uses_native_result_and_emails_it(rig):
    _,router,sender=rig
    router.return_value=decision(detail=True,domain='travel')
    first=run(rig,'I want to go travel to Melbourne. Could you give me some advice on that?')
    done(first,'Which month, how many days, and where will you travel from?')
    trip=ContinuityStore().current('s')
    router.return_value=decision(target=trip['id'],detail=True,domain='travel')
    second=run(rig,'I will be traveling from Sydney and I will be going there in October this year and I think I have five days')
    body='A useful five-day Melbourne itinerary with Sydney transport guidance.'
    result=done(second,body)
    assert not result.get('failed') and 'submitted' in result['final_response']
    sender.assert_called_once_with('xiaoheng.hong@intel.com',body)


def test_open_ended_advice_keeps_brief_budget_and_avoids_optional_tools(rig):
    rig[1].return_value=decision(detail=False,domain='travel',execution='research')
    value=run(rig,'Could you give me some advice about Melbourne?')
    policy=value['continuation']
    assert policy.max_api_calls==6 and policy.max_output_tokens==512
    assert 'Answer from stable knowledge without tools' in policy.context
    assert policy.before_tool('web_search',{'query':'one'}) is None
    assert policy.before_tool('web_search',{'query':'two'})['action']=='block'


def test_distinct_essential_questions_are_allowed_but_exact_repeat_stops(rig):
    _,router,sender=rig
    router.return_value=decision(detail=True)
    done(run(rig,'Prepare a client workshop.'),'How long should the workshop be?')
    task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],detail=True)
    second=run(rig,'One hour.')
    done(second,'How many people will attend?')
    pending=ContinuityStore().pending('s')
    assert pending and pending['payload']['question']=='How many people will attend?'
    router.return_value=decision(target=task['id'],detail=True)
    repeated=done(run(rig,'Twenty people.'),'How many people will attend?')
    assert repeated['failed'] and 'I have your answer' in repeated['final_response']
    assert ContinuityStore().pending('s') is None and sender.call_count==0


def test_router_failure_without_pending_falls_back_to_native_harness(rig):
    rig[1].side_effect=ValueError('router unavailable')
    assert run(rig,'Explain photosynthesis.') is None
    rig[2].assert_not_called()


def test_sources_come_from_successful_current_tools_not_generated_urls(rig):
    rig[1].return_value=decision(detail=True)
    value=run(rig);policy=value['continuation']
    policy.after_tool('web_search',{}, {'error':'failed','url':'https://failed.example/'})
    policy.after_tool('web_search',{}, {'results':[{'url':'https://source.example/page','description':'Retrieved factual excerpt.'}]})
    done(value,'Useful answer. https://invented.example/')
    store=ContinuityStore();task=store.current('s')
    assert store.result('s',task['id'])['sources']==['https://source.example/page']
    assert 'invented.example' not in store.result('s',task['id'])['body']
    assert 'https://source.example/page' in store.result('s',task['id'])['body']


def test_expansion_retains_own_prior_and_new_evidence_not_other_topic(rig):
    _,router,_=rig
    first=run(rig)
    first['continuation'].after_tool('web_search',{}, {'url':'https://prior.example/'})
    done(first);store=ContinuityStore();task=store.current('s')
    router.return_value=decision(target=task['id'],operation='expand',detail=True)
    second=run(rig,'Give me more detail without email.')
    second['continuation'].after_tool('web_search',{}, {'url':'https://new.example/','description':'Expanded factual excerpt.'})
    done(second,'Expanded report. https://new.example/')
    assert store.result('s',task['id'])['sources']==['https://new.example/','https://prior.example/']


def test_explicit_unselected_result_preserves_delivery_without_guessing(rig):
    _,router,sender=rig
    done(run(rig,'Draft an engineering agenda.'))
    done(run(rig,'Draft a marketing agenda.'))
    before=router.call_count
    reply=run(rig,"Email one of the two agendas to other@example.com. I haven't decided which one.")
    pending=ContinuityStore().pending('s')
    assert reply['final_response'].endswith('?') and router.call_count==before
    assert pending['kind']=='reference' and 'other@example.com' in pending['payload']['request']
    sender.assert_not_called()


def test_send_cannot_generate_a_substitute_when_original_result_is_missing(rig):
    _,router,sender=rig
    value=run(rig)
    value['continuation'].finalize(response_text='',failed=True,turn_exit_reason='error',messages=[])
    task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],operation='send',delivery='email')
    result=run(rig,'Email the earlier report.')
    assert result['failed'] and 'continuation' not in result
    sender.assert_not_called()


def test_named_result_recipient_question_uses_host_slot(rig):
    _,router,sender=rig
    done(run(rig));task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],operation='send',delivery='email',question='What is the new email address?')
    run(rig,'Send the restaurant list to another email address.')
    assert ContinuityStore().pending('s')['kind']=='recipient'
    before=router.call_count
    run(rig,'other@example.com','text')
    assert router.call_count==before and sender.call_count==1


def test_failed_research_retains_useful_result_with_verification_note(rig):
    rig[1].return_value=decision(detail=True,execution='research')
    value=run(rig,'Research a current topic.')
    value['continuation'].after_tool('web_search',{}, {'error':'offline'})
    result=done(value,'Unsupported detailed claims.')
    assert not result.get('failed') and 'No automatic email was sent' in result['final_response']
    assert result['final_response'].startswith('I could not retrieve live sources')
    rig[2].assert_not_called()
    task=ContinuityStore().current('s')
    assert 'Verification note:' in ContinuityStore().result('s',task['id'])['body']


def test_truncated_simple_answer_is_salvaged_without_email(rig):
    value=run(rig,'Give me brief advice.')
    result=value['continuation'].finalize(response_text='Useful advice followed by excess detail.',failed=True,
                                          turn_exit_reason='workflow_incomplete_output',messages=[])
    assert not result.get('failed') and result['final_response']=='A useful summary.'
    rig[2].assert_not_called()


def test_exhausted_read_only_tool_loop_gets_one_text_only_recovery(rig,monkeypatch):
    rig[1].return_value=decision(detail=True,execution='research')
    recover=Mock(return_value='Recovered report from successful evidence.')
    monkeypatch.setattr(base,'_recover_tool_result',recover)
    value=run(rig,'Research and prepare a detailed report.')
    value['continuation'].after_tool('web_search',{}, {'url':'https://source.example/','description':'Evidence.'})
    result=value['continuation'].finalize(response_text='',failed=True,turn_exit_reason='unknown',
                                          messages=[{'role':'tool','name':'web_search','content':'Evidence.'}])
    assert not result.get('failed') and 'submitted' in result['final_response']
    recover.assert_called_once()
    assert 'Recovered report' in rig[2].call_args.args[1]


def test_summarizer_cannot_replace_native_research_with_extractive_notes(rig,monkeypatch):
    rig[1].return_value=decision(detail=True,execution='research')
    validator=Mock(side_effect=ValueError('Summary unavailable'))
    monkeypatch.setattr(base,'_summary',validator)
    value=run(rig,'Research the options and email the details.')
    value['continuation'].after_tool('web_search',{}, {'data':{'web':[{'title':'Store A','url':'https://a.example/','description':'Store A is in Boston.'}]}})
    reply=done(value,'Store B is in Boston. https://a.example/')
    body=rig[2].call_args.args[1]
    assert 'Store B is in Boston.' in body and 'https://a.example/' in body
    assert 'submitted' in reply['final_response']
    assert validator.call_count==1


def test_grounding_label_from_summarizer_cannot_veto_native_result(rig,monkeypatch):
    rig[1].return_value=decision(detail=True,execution='research')
    monkeypatch.setattr(base,'_summary',Mock(side_effect=ValueError('Summary unavailable')))
    value=run(rig,'Research the options.')
    value['continuation'].after_tool('web_search',{}, {'url':'https://a.example/'})
    assert 'submitted' in done(value,'Unverified specifics. https://a.example/')['final_response']
    assert 'Unverified specifics.' in rig[2].call_args.args[1]


def test_summary_timeout_retains_native_result_and_observed_sources(rig,monkeypatch):
    rig[1].return_value=decision(detail=True,execution='research')
    monkeypatch.setattr(base,'_summary',Mock(side_effect=TimeoutError()))
    value=run(rig,'Research the options.')
    value['continuation'].after_tool('web_search',{}, {'url':'https://a.example/','description':'Evidence.'})
    reply=done(value,'Specific claims. https://a.example/')
    assert 'submitted' in reply['final_response']
    body=rig[2].call_args.args[1]
    assert 'Specific claims' in body and 'https://a.example/' in body


def test_literal_malformed_mailbox_question_is_a_recipient_slot(rig):
    _,router,sender=rig
    done(run(rig));task=ContinuityStore().current('s')
    router.return_value=decision(target=task['id'],operation='send',delivery='email',question='xiaoheng.hong.intel.com')
    assert run(rig,'Send it to xiaoheng.hong.intel.com')['final_response']=='Did you mean xiaoheng.hong@intel.com?'
    run(rig,'Yes')
    sender.assert_called_once()


@pytest.mark.parametrize('no_email',[False,True])
def test_interrupted_unfinished_trip_uses_native_harness_and_retains_delivery_intent(rig,no_email):
    rig[1].return_value=decision(detail=True,domain='travel')
    first=run(rig,'Plan a trip to New York from Vancouver.'+(' Do not email anything.' if no_email else ''))
    done(first,'Which month and how many days?')
    store=ContinuityStore();trip=store.current('s')
    rig[1].return_value=decision()
    done(run(rig,'What does RSVP mean?'),'Please respond.')
    rig[1].return_value=decision(target=trip['id'],operation='expand',detail=True,domain='travel')
    reply=run(rig,'Back to New York: December, four days.')
    done(reply,'The complete New York itinerary.')
    assert 'continuation' in reply
    assert store.result('s',trip['id'])['body']=='The complete New York itinerary.'
    assert rig[2].call_count==(0 if no_email else 1)
