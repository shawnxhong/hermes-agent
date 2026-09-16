"""Migrated legacy-router contracts, without obsolete scene/keyword heuristics."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from hermes_cli.voice_continuity_router import route
from hermes_cli.voice_continuity_store import ContinuityStore
from hermes_cli import general_voice


def agent(values):
    client=Mock();client.with_options.return_value=client
    client.chat.completions.create.side_effect=[NS(choices=[NS(finish_reason='stop',message=NS(
        tool_calls=None,content=json.dumps(v)))]) for v in values]
    return NS(client=client,base_url='http://localhost:8000/v3',model='local',_interrupt_requested=False)


def value(**changes):
    return dict(dict(execution='content',relation='independent',operation='answer',
                     target='NEW',detail=False,delivery='none',version=0,question=''),**changes)


@pytest.mark.parametrize('platform,modality',[('cli','text'),('feishu','voice'),('feishu','text')])
def test_nonvoice_and_im_never_route(monkeypatch,platform,modality):
    monkeypatch.setattr(general_voice,'config',lambda:{'enabled':True})
    a=agent([])
    assert general_voice.run_workflow(agent=a,user_message='Plan a workshop',session_id='s',
        input_modality=modality,platform=platform) is None
    a.client.with_options.assert_not_called()


@pytest.mark.parametrize('kind',['coding','action'])
def test_native_permissions_not_replaced(kind):
    a=agent([value(execution=kind,detail=True)])
    assert route(a,'Do this task',ContinuityStore(),'s',None)['operation']=='native'


def test_one_structured_call_and_new_task_no_forced_question():
    a=agent([value(detail=True,question='Who is attending?')])
    result=route(a,'Prepare a workshop',ContinuityStore(),'s',None)
    assert result['question']=='' and result['detail']
    kwargs=a.client.chat.completions.create.call_args.kwargs
    assert not kwargs['stream'] and 'tools' not in kwargs
    assert kwargs['response_format']['type']=='json_schema'
    assert a.client.with_options.call_args.kwargs['max_retries']==0


def test_invalid_json_repairs_once_without_mutating_tasks():
    store=ContinuityStore();a=agent([{},{}])
    with pytest.raises(ValueError):route(a,'Plan a workshop',store,'s',None)
    assert a.client.chat.completions.create.call_count==2 and store.current('s') is None


@pytest.mark.parametrize('cloud',[False,True])
def test_cancelled_or_remote_route_never_calls_provider(cloud):
    a=agent([])
    if cloud:a.base_url='https://example.com/v1'
    else:a._interrupt_requested=True
    with pytest.raises(ValueError):route(a,'Plan a workshop',ContinuityStore(),'s',None)
    a.client.with_options.assert_not_called()


def test_unfinished_task_can_be_selected_without_saved_artifact():
    store=ContinuityStore();task=store.start('s','Prepare a workshop')
    a=agent([value(relation='followup',target='T1: Prepare a workshop',detail=True)])
    assert route(a,'Twenty people, one hour.',store,'s',None)['target']==task['id']


def test_catalog_omits_saved_body_and_summary_and_limits_recent_turns():
    store=ContinuityStore();task=store.start('s','Report')
    store.save_result('s',task,body='SECRET BODY',summary='OLD NARRATION')
    for i in range(5):store.record_turn('s',f'request {i}',f'reply {i}')
    a=agent([value()]);route(a,'Hello',store,'s',None)
    prompt=a.client.chat.completions.create.call_args.kwargs['messages'][1]['content']
    assert 'SECRET BODY' not in prompt and 'OLD NARRATION' not in prompt
    assert 'request 2' not in prompt and 'request 3' in prompt and 'request 4' in prompt
