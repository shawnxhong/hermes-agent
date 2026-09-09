import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli.voice_continuity_router import route
from hermes_cli.voice_continuity_store import ContinuityStore


def response(**changes):
    value=dict(relation='followup',summary='Send the meeting agenda',operation='send',
               target='T1: Meeting agenda',detail=False,delivery='email',version=0,question='',
               language='en',domain='general')
    value.update(changes)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(tool_calls=None,content=json.dumps(value)))])


def agent_for(*responses):
    client=Mock()
    client.with_options.return_value=client
    client.chat.completions.create.side_effect=responses
    return SimpleNamespace(client=client,model='local',base_url='http://localhost:8000/v3',
                           _interrupt_requested=False)


def test_short_handle_resolves_only_to_owned_topic_and_is_not_persisted():
    store=ContinuityStore();task=store.start('s','Meeting agenda')
    store.start('other','Private unrelated report')
    agent=agent_for(response())
    result=route(agent,'Email that agenda.',store,'s',None)
    assert result['target']==task['id']
    sent=agent.client.chat.completions.create.call_args.kwargs
    assert task['id'] not in sent['messages'][1]['content']
    assert sent['response_format']['json_schema']['schema']['properties']['target']['enum']==['NEW','T1: Meeting agenda']


def test_independent_intent_cannot_overwrite_selected_previous_document():
    store=ContinuityStore();old=store.start('s','Shopping checklist')
    agent=agent_for(response(relation='independent',operation='revise',summary='Draft meeting agenda'))
    result=route(agent,'Draft a meeting agenda.',store,'s',None)
    assert result['target']=='NEW' and result['operation']=='answer' and result['version']==0
    assert store.current('s')['id']==old['id']


def test_invalid_handle_retries_once_then_fails_without_modifying_state():
    store=ContinuityStore();task=store.start('s','Meeting agenda')
    agent=agent_for(response(target='T99'),response(target='T99'))
    with pytest.raises(ValueError,match='routing invalid'):
        route(agent,'Email that agenda.',store,'s',None)
    assert agent.client.chat.completions.create.call_count==2
    assert store.current('s')==task


def test_routing_rejects_cloud_endpoint_before_request():
    agent=agent_for(response());agent.base_url='https://example.com/v1'
    with pytest.raises(ValueError,match='local inference'):
        route(agent,'Hello',ContinuityStore(),'s',None)
    agent.client.with_options.assert_not_called()


def test_predicted_new_version_is_repaired_before_native_execution():
    store=ContinuityStore();task=store.start('s','Meeting agenda')
    task,_=store.save_result('s',task,body='Original meeting agenda.',summary='Meeting agenda.',detailed=True)
    agent=agent_for(response(operation='revise',version=2),response(operation='revise',version=0))
    result=route(agent,'Make the agenda shorter.',store,'s',None)
    assert result['version']==0 and result['api_calls']==2
    assert store.result('s',task['id'])['body']=='Original meeting agenda.'
