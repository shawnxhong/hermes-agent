import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from hermes_cli.voice_task_router import route_task,validate_route,RoutingError


def result(**kw):
    return dict({'intent':'complex','relation':'new','task_summary':'Prepare a workshop',
                 'question':'Who is the audience?','language':'en','domain':'general'},**kw)


def agent(responses):
    def response(item):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(
            tool_calls=None,content=json.dumps(item)))])
    client=Mock();client.with_options.return_value.chat.completions.create.side_effect=[response(r) for r in responses]
    return SimpleNamespace(client=client,base_url='http://localhost:8000/v3',model='local-qwen',_interrupt_requested=False)


def test_simple_and_complete_requests_never_force_two_rounds():
    assert validate_route(result(intent='simple'),None)['question']==''
    assert validate_route(result(question=''),None)['route']=='execute'
    assert validate_route(result(),None)['route']=='execute'


def test_continuation_never_repeats_requirements_question():
    active={'phase':'awaiting_details','question_used':True}
    actual=validate_route(result(relation='answer'),active)
    assert actual['route']=='execute' and not actual['question']
    active.update(phase='result_ready',artifact_version=1)
    assert validate_route(result(relation='followup'),active)['route']=='followup'
    with pytest.raises(RoutingError):validate_route(result(relation='followup'),None)


@pytest.mark.parametrize('intent',['coding','action','other'])
def test_router_does_not_replace_native_permissions(intent):
    assert validate_route(result(intent=intent),None)['route']=='native'


@pytest.mark.parametrize('platform,modality',[('cli','text'),('feishu','voice'),('feishu','text')])
def test_nonvoice_and_im_have_no_routing_call(platform,modality):
    a=agent([])
    assert route_task(a,'Plan a workshop',platform=platform,modality=modality) is None
    a.client.with_options.assert_not_called()


def test_local_routing_uses_structured_nonstreaming_request():
    a=agent([result()])
    assert route_task(a,'Plan a workshop',platform='cli',modality='voice')['api_calls']==1
    call=a.client.with_options.return_value.chat.completions.create.call_args.kwargs
    assert not call['stream'] and 'tools' not in call
    assert call['response_format']['type']=='json_schema'
    assert a.client.with_options.call_args.kwargs['max_retries']==0


def test_open_ended_advice_is_presentation_brief_not_a_domain_restriction():
    a=agent([result(question='')])
    actual=route_task(a,'Could you give me some suggestions for Melbourne?',platform='cli',modality='voice')
    assert actual['intent']=='simple' and actual['route']=='simple'
    a=agent([result(question='')])
    actual=route_task(a,'Create a detailed Melbourne itinerary.',platform='cli',modality='voice')
    assert actual['intent']=='complex' and actual['route']=='execute'


def test_invalid_json_has_only_one_repair():
    a=agent([{},{}])
    with pytest.raises(RoutingError):route_task(a,'Plan a workshop',platform='cli',modality='voice')
    assert a.client.with_options.return_value.chat.completions.create.call_count==2


def test_cloud_or_cancelled_router_does_not_call_provider():
    a=agent([]);a.base_url='https://cloud.example/v1'
    with pytest.raises(RoutingError):route_task(a,'Plan a workshop',platform='cli',modality='voice')
    a.base_url='http://localhost:8000/v3';a._interrupt_requested=True
    with pytest.raises(RoutingError):route_task(a,'Plan a workshop',platform='cli',modality='voice')
    a.client.with_options.assert_not_called()


def test_continuation_before_first_artifact_completes_requirements():
    active={'phase':'awaiting_details','question_used':True,'artifact_version':None}
    actual=validate_route(result(relation='followup'),active)
    assert actual['relation']=='answer' and actual['route']=='execute'
    active.update(phase='result_ready',artifact_version=1)
    assert validate_route(result(relation='followup'),active)['route']=='followup'


def test_explicit_cancellation_wins_over_uncertain_task_kind():
    assert validate_route(result(intent='other',relation='cancel'),None)['route']=='cancel'


def test_explicit_new_task_control_overrides_wrong_model_continuation():
    a=agent([result(relation='followup',question='')])
    active={'phase':'result_ready','artifact_version':1}
    actual=route_task(a,'Now draft a welcome message for new employees.',active,platform='cli',modality='voice')
    assert actual['relation']=='new' and actual['route']=='execute'
    a=agent([result(relation='followup',question='')])
    actual=route_task(a,'Now write a shorter version of it.',active,platform='cli',modality='voice')
    assert actual['relation']=='followup'


def test_explicit_reference_answers_pending_question_despite_new_label():
    a=agent([result(domain='travel',question='')])
    active={'phase':'awaiting_details','question_used':True,'request':'Travel to New York'}
    actual=route_task(a,'I will travel there in December for four days from Vancouver.',active,platform='cli',modality='voice')
    assert actual['relation']=='answer' and actual['route']=='execute'


def test_spoken_travel_answer_keeps_strategy_despite_general_new_label():
    a=agent([result(domain='general',question='What is your budget?')])
    active={'phase':'awaiting_details','domain':'travel','question_used':True,'request':'Travel to Sydney'}
    actual=route_task(a,"I will be traveling there in December and I will be having like one week and I'll be traveling from Melbourne.",active,platform='cli',modality='voice')
    assert actual['relation']=='answer' and actual['domain']=='travel'
    assert actual['route']=='execute' and not actual['question']


def test_answer_label_after_result_is_followup_not_fatal():
    actual=validate_route(result(relation='answer'),{'phase':'result_ready','artifact_version':1})
    assert actual['relation']=='followup' and not actual['question']


def test_referential_explanation_preserves_active_artifact():
    a=agent([result(intent='simple',domain='travel',question='')])
    active={'phase':'result_ready','artifact_version':1}
    actual=route_task(a,'Why is flying the best way to get there?',active,platform='cli',modality='voice')
    assert actual['relation']=='followup' and actual['route']=='simple'
