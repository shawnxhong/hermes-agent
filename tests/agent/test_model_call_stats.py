"""Measurements stay truthful, isolated and outside model request content."""
import json
import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from agent.model_call_stats import ModelCallStats


class Clock:
    value = 0.0
    def __call__(self):
        return self.value


def collector(clock=None, request_id='r'):
    return ModelCallStats(request_id=request_id, session_id='s', platform='feishu',
                          model='test', provider='custom', attempt=1, clock=clock or Clock())


def records(caplog):
    return [json.loads(r.getMessage().split('model_call_stats ', 1)[1])
            for r in caplog.records if r.name == 'agent.model_call_stats']


@pytest.fixture(autouse=True)
def logs(caplog):
    caplog.set_level(logging.INFO, logger='agent.model_call_stats')


@pytest.mark.parametrize('delta', [{'content':'a'}, {'reasoning_content':'think'},
    {'tool_calls':[{'function':{'name':'local_media','arguments':''}}]}])
def test_ttft_ignores_role_only_usage_and_terminal_chunks(caplog, delta):
    clock=Clock(); c=collector(clock)
    clock.value=1; c.observe_chunk({'choices':[{'delta':{'role':'assistant','content':''}}]})
    clock.value=2; c.observe_chunk({'choices':[], 'usage':{'prompt_tokens':100}})
    clock.value=3; c.observe_chunk({'choices':[{'delta':delta}]})
    clock.value=5; c.observe_chunk({'choices':[{'delta':{'content':'later'}}]})
    c.finish()
    r=records(caplog)[0]
    assert r['ttft_ms']==3000 and r['duration_ms']==5000
    assert r['input_tokens']==100
    assert r['queue_ms'] is None and r['cached_input_tokens'] is None


def test_explicit_zero_is_distinct_from_missing(caplog):
    c=collector(); c.finish(NS(usage=NS(prompt_tokens=12, completion_tokens=2,total_tokens=14,
        prompt_tokens_details=NS(cached_tokens=0),queue_time=0)))
    r=records(caplog)[0]
    assert r['cached_input_tokens']==0 and r['queue_ms']==0
    assert r['cache_source']!='unavailable' and r['queue_source']!='unavailable'
    assert r['ttft_ms'] is None


@pytest.mark.parametrize('bad', [None, -1, True, '42', float('nan'), float('inf'), 0.5])
def test_invalid_token_measurements_stay_unavailable(caplog, bad):
    c=collector(); c.finish({'usage':{'prompt_tokens':bad,
        'prompt_tokens_details':{'cached_tokens':bad}}})
    r=records(caplog)[0]
    assert r['input_tokens'] is None and r['cached_input_tokens'] is None


def test_headers_are_allowlisted_units_are_explicit(caplog):
    c=collector(); c.observe_headers({'x-request-id':'server-r','x-queue-time-ms':'23.5',
        'authorization':'SECRET','set-cookie':'SECRET'})
    c.finish({'usage':{'queue_time':0.025}})
    r=records(caplog)[0]
    assert r['provider_request_id']=='server-r' and r['queue_ms']==25
    assert 'SECRET' not in caplog.text


def test_late_chunks_duplicate_finish_and_other_calls_do_not_leak(caplog):
    c=collector(request_id='old'); c.finish(error_type='TimeoutError')
    c.observe_chunk({'choices':[{'delta':{'content':'late'}}], 'usage':{'prompt_tokens':999}})
    c.finish()
    other=collector(request_id='new'); other.finish()
    rows=records(caplog)
    assert len(rows)==2 and [r['request_id'] for r in rows]==['old','new']
    assert all(r['ttft_ms'] is None and r['input_tokens'] is None for r in rows)
    assert rows[0]['error_type']=='TimeoutError'


def test_logging_failure_is_not_a_model_failure(monkeypatch):
    monkeypatch.setattr('agent.model_call_stats.log.info',Mock(side_effect=OSError('disk full')))
    collector().finish()


@pytest.mark.parametrize('fault', [None, 'constructor', 'finish', 'logging'])
@pytest.mark.parametrize('streaming', [False, True])
def test_actual_native_loop_records_once_without_changing_answer(caplog, monkeypatch, streaming, fault):
    import httpx
    from openai import OpenAI
    from run_agent import AIAgent
    monkeypatch.setattr('agent.title_generator.maybe_auto_title', lambda *a, **kw:None)
    usage={'prompt_tokens':50,'completion_tokens':1,'total_tokens':51,
           'prompt_tokens_details':{'cached_tokens':40}}
    requests=[]
    def handler(request):
        body=json.loads(request.content)
        requests.append(body)
        assert 'model_call_stats' not in json.dumps(body)
        if body.get('stream'):
            chunks=[{'id':'provider-r','object':'chat.completion.chunk','created':1,'model':'test',
                     'choices':[{'index':0,'delta':{'role':'assistant','content':''},'finish_reason':None}]},
                    {'id':'provider-r','object':'chat.completion.chunk','created':1,'model':'test',
                     'choices':[{'index':0,'delta':{'content':'Hello.'},'finish_reason':None}]},
                    {'id':'provider-r','object':'chat.completion.chunk','created':1,'model':'test',
                     'choices':[{'index':0,'delta':{},'finish_reason':'stop'}],'usage':usage}]
            s=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
            return httpx.Response(200,headers={'content-type':'text/event-stream','x-request-id':'provider-r',
                'x-queue-time-ms':'5'},content=s)
        return httpx.Response(200,json={'id':'provider-r','object':'chat.completion','created':1,'model':'test',
            'choices':[{'index':0,'message':{'role':'assistant','content':'Hello.'},'finish_reason':'stop'}],
            'usage':usage})
    client=OpenAI(api_key='test',base_url='http://local.test/v1',
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    agent=AIAgent(model='test',provider='custom',base_url='http://local.test/v1',api_key='test',
        api_mode='chat_completions',quiet_mode=True,max_iterations=2,platform='cli',
        skip_memory=True,skip_context_files=True,skip_background_review=True,session_id='stats-native')
    if streaming:
        agent.client=client
        monkeypatch.setattr(agent,'_create_request_openai_client',lambda **kw:client)
        monkeypatch.setattr(agent,'_close_request_openai_client',lambda *a,**kw:None)
    else:
        agent.client=Mock(spec=['chat','is_closed','close']); agent.client.is_closed=False
        from openai.types.chat import ChatCompletion
        agent.client.chat.completions.create=Mock(return_value=ChatCompletion.model_validate(handler(
            httpx.Request('POST','http://local.test',json={})).json()))
    if fault == 'constructor':
        monkeypatch.setattr('agent.model_call_stats.ModelCallStats', Mock(side_effect=OSError('fixture init')))
    elif fault == 'finish':
        monkeypatch.setattr(ModelCallStats, 'finish', Mock(side_effect=OSError('fixture finish')))
    elif fault == 'logging':
        monkeypatch.setattr('agent.model_call_stats.log.info', Mock(side_effect=OSError('fixture disk full')))
    result=agent.run_conversation('Hello')
    assert result['final_response']=='Hello.'
    assert len(requests)==1
    assert getattr(agent, '_model_call_stats', None) is None
    if fault:
        assert not records(caplog)
        client.close()
        return
    rows=records(caplog); assert len(rows)==1
    r=rows[0]
    assert r['input_tokens']==50 and r['output_tokens']==1 and r['cached_input_tokens']==40
    assert r['request_id'].startswith('stats-native:') and r['attempt']==1
    assert (r['ttft_ms'] is not None)==streaming
    assert r['queue_ms']==(5 if streaming else None)
    client.close()
