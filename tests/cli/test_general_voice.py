import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from hermes_cli import general_voice as voice
from hermes_cli.voice_delivery import TaskStore


def routing(**kw):
    return dict({'intent':'complex','relation':'new','route':'execute','task_summary':'Draft a report',
                 'question':'','language':'en','domain':'general','api_calls':1},**kw)


@pytest.fixture
def rig(monkeypatch):
    cfg={'enabled':True,'default_recipient':'default@example.com'}
    monkeypatch.setattr(voice,'config',lambda:cfg)
    monkeypatch.setattr(voice,'travel_plugin',lambda:None)
    router=Mock(return_value=routing());monkeypatch.setattr(voice,'route_task',router)
    sender=Mock(return_value={'success':True});monkeypatch.setattr(voice,'_send',sender)
    summary=Mock(return_value='The report covers the requested topic and next steps.');monkeypatch.setattr(voice,'_summary',summary)
    agent=SimpleNamespace(base_url='http://localhost:8000/v3',_interrupt_requested=False,session_id='general-test')
    return cfg,agent,router,sender,summary


def run(rig,text='Draft a report.',modality='voice',platform='cli'):
    return voice.run_workflow(agent=rig[1],user_message=text,session_id=rig[1].session_id,input_modality=modality,platform=platform)


def complete(policy,text='The complete detailed report.',failed=False,reason='text_response(finish_reason=stop)'):
    return policy['continuation'].finalize(response_text=text,failed=failed,turn_exit_reason=reason,messages=[])


def test_complete_task_executes_then_summarizes_and_sends(rig):
    _,_,router,sender,summary=rig
    policy=run(rig)
    sender.assert_not_called()
    result=complete(policy)
    sender.assert_called_once_with('default@example.com','The complete detailed report.')
    assert result['final_response'].endswith('submitted for email delivery.')
    assert summary.call_count==1


def test_requirements_question_once_then_native_execution_same_task(rig):
    _,agent,router,sender,_=rig
    router.return_value=routing(route='ask',question='Who is the audience?')
    assert run(rig)['final_response'].endswith('?')
    first=TaskStore().current(agent.session_id)
    assert first['pending_question']=='Who is the audience?'
    router.return_value=routing(relation='answer')
    policy=run(rig,'Twenty colleagues.')
    assert TaskStore().current(agent.session_id)['id']==first['id']
    assert 'Twenty colleagues.' in policy['continuation'].context
    complete(policy)
    assert sender.call_count==1


def test_short_explanation_keeps_artifact_and_does_not_email(rig):
    _,agent,router,sender,_=rig
    complete(run(rig));first=TaskStore().current(agent.session_id)
    router.return_value=routing(intent='simple',relation='followup',route='simple')
    result=complete(run(rig,'Why?'),'Because it helps.')
    assert result['final_response']=='Because it helps.' and sender.call_count==1
    assert TaskStore().current(agent.session_id)['artifact_version']==first['artifact_version']


def test_typed_redirect_reuses_artifact_and_new_task_resets_recipient(rig):
    cfg,agent,router,sender,_=rig
    complete(run(rig));first=TaskStore().current(agent.session_id)
    assert run(rig,'Send it to another email address.')['final_response'].endswith('?')
    assert run(rig,'other@example.com',modality='text')['handled']
    assert router.call_count==1 and sender.call_args.args[0]=='other@example.com'
    assert sender.call_args.args[1]=='The complete detailed report.'
    router.return_value=routing()
    complete(run(rig,'Now draft a different report.'),'The new report.')
    assert TaskStore().current(agent.session_id)['id']!=first['id']
    assert sender.call_args.args[0]==cfg['default_recipient']


@pytest.mark.parametrize('platform,modality',[('cli','text'),('feishu','voice'),('feishu','text')])
def test_nonvoice_entry_does_not_trigger_workflow(rig,platform,modality):
    assert run(rig,modality=modality,platform=platform) is None
    rig[2].assert_not_called();rig[3].assert_not_called()


def test_coding_preserves_native_harness(rig):
    rig[2].return_value=routing(intent='coding',route='native')
    assert run(rig,'Write Python code.') is None
    rig[3].assert_not_called()


@pytest.mark.parametrize('reason',['workflow_execution_budget','partial_stream_recovery','text_response(finish_reason=length)'])
def test_incomplete_execution_never_emails(rig,reason):
    result=complete(run(rig),reason=reason)
    assert result['failed'] and 'No automatic result email' in result['final_response']
    rig[3].assert_not_called();rig[4].assert_not_called()


def test_sender_failure_and_summary_failure_are_honest(rig):
    rig[3].return_value={'error':'timeout'};rig[4].side_effect=ValueError('bad summary')
    result=complete(run(rig))
    assert 'not confirmed' in result['final_response'] and 'submitted' not in result['final_response']
    assert TaskStore().artifact(rig[1].session_id,TaskStore().current(rig[1].session_id)['id'])['body']


def test_explicit_no_email_and_missing_recipient(rig):
    result=complete(run(rig,"Draft a report but don't email it."))
    assert 'not emailed' in result['final_response'];rig[3].assert_not_called()
    rig[0]['default_recipient']=''
    result=complete(run(rig,'Draft another report.'))
    assert result['final_response'].endswith('?');rig[3].assert_not_called()


def test_new_task_and_interrupt_block_stale_delivery(rig):
    old=run(rig)
    run(rig,'Draft a new task.')
    with pytest.raises(Exception):complete(old)
    rig[3].assert_not_called()
    current=run(rig);rig[1]._interrupt_requested=True
    with pytest.raises(RuntimeError):complete(current)
    rig[3].assert_not_called()


def test_tool_budget_and_retries_are_enforced_without_schema_changes(rig):
    policy=run(rig)['continuation']
    assert policy.before_tool('web_search',{'query':'one'}) is None
    assert policy.before_tool('web_search',{'query':'one'})['action']=='block'
    policy.after_tool('web_search',{},json.dumps({'error':'offline'}))
    assert policy.before_tool('web_search',{'query':'different'})['action']=='block'
    assert policy.before_tool('send_message',{'target':'email:other@example.com'})['action']=='block'
    assert policy.before_tool('memory',{'action':'replace'})['action']=='block'


def test_native_action_keeps_original_message_and_memory_permissions(rig):
    rig[2].return_value=routing(intent='action',route='native')
    policy=run(rig,'Email my colleague the requested message.')['continuation']
    assert policy.before_tool('send_message',{'target':'email:colleague@example.com'}) is None
    assert policy.before_tool('memory',{'action':'add'}) is None


def test_brief_validation_does_not_validate_a_truncated_preview():
    assert voice._brief('A useful brief answer.')
    assert not voice._brief('Short. Short. Short. ' + 'Further detail. '*40)
    assert not voice._brief('Details ' *150)


def test_explanation_cannot_claim_an_email_that_did_not_happen(rig):
    complete(run(rig))
    rig[2].return_value=routing(intent='simple',relation='followup',route='simple')
    result=complete(run(rig,'Why?'),'Practice helps. Email sent.')
    assert result['final_response']=='Practice helps.'
    assert rig[3].call_count==1


def test_travel_suggestions_keep_tested_strategy_even_if_router_says_simple(rig,monkeypatch):
    travel=SimpleNamespace(_load=lambda session:{},run_workflow=Mock(return_value={'handled':True,'final_response':'When will you go?','api_calls':1}))
    monkeypatch.setattr(voice,'travel_plugin',lambda:travel)
    rig[2].return_value=routing(intent='simple',domain='travel',route='simple')
    result=run(rig,'I want to visit New York. Any suggestions?')
    assert result['final_response']=='When will you go?' and result['api_calls']==2
    travel.run_workflow.assert_called_once()
    rig[3].assert_not_called()


def test_silent_control_marker_is_not_an_email_deliverable(rig):
    result=complete(run(rig),'NO_REPLY_EXPECTED')
    assert result['failed'] and 'No automatic email' in result['final_response']
    rig[3].assert_not_called()


def test_long_explanation_is_summarized_without_replacing_or_emailing_report(rig):
    complete(run(rig));task=TaskStore().current(rig[1].session_id)
    rig[2].return_value=routing(intent='simple',relation='followup',route='simple')
    result=complete(run(rig,'Why?'),'Detailed explanation. '*50)
    assert rig[3].call_count==1 and rig[4].call_count==2
    assert 'submitted' not in result['final_response']
    assert TaskStore().current(rig[1].session_id)['artifact_version']==task['artifact_version']


def test_request_for_more_details_is_not_emailed_as_a_completed_report(rig):
    result=complete(run(rig),'It looks like your message is incomplete. Could you clarify the goal and duration?')
    assert result['failed']
    rig[3].assert_not_called()


def test_delivery_validation_can_reject_a_metacommentary_result(rig):
    rig[4].side_effect=voice.IncompleteResult('Not a report')
    result=complete(run(rig),'The assistant would produce a report after receiving more details.')
    assert result['failed']
    rig[3].assert_not_called()
