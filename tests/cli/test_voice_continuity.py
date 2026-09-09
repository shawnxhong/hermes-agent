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
    monkeypatch.setattr(base,'travel_plugin',lambda:None)
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


def test_expansion_without_email_saves_and_explanation_keeps_main(rig):
    _,router,sender=rig
    done(run(rig));store=ContinuityStore();task=store.current('s')
    router.return_value=decision(target=task['id'],operation='expand',detail=True)
    done(run(rig,'More detail please.'),'Expanded content.')
    main=store.current('s')['artifact_version']
    router.return_value=decision(target=task['id'],operation='explain')
    done(run(rig,'Why those choices?'),'Because they suit your request.')
    assert store.current('s')['artifact_version']==main
    sender.assert_not_called()


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


def test_ambiguous_reference_retains_original_send_request(rig):
    _,router,sender=rig
    done(run(rig));store=ContinuityStore();task=store.current('s')
    router.return_value=decision(target=task['id'],operation='send',delivery='email',question='Do you mean the restaurants?')
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
    run(rig,'Draft a meeting agenda.')
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
