"""Real SSE decoding + Hermes accumulation, with no external inference/tools."""
import json
from types import SimpleNamespace as NS
from unittest.mock import patch

import httpx
import pytest
from openai import OpenAI

from agent.tool_protocol_diag import ProtocolCapture, begin_request, execution_event


def frame(index=0, identity='call_a', name='demo_home_status', args='{}', finish=None):
    return {'id':'completion','object':'chat.completion.chunk','created':0,'model':'local',
            'choices':[{'index':0,'delta':{'tool_calls':[{'index':index,'id':identity,
                'type':'function','function':{'name':name,'arguments':args}}]},'finish_reason':finish}]}


def wire(frames, done=True):
    return b''.join(b'data: '+json.dumps(f).encode()+b'\n\n' for f in frames)+(b'data: [DONE]\n\n' if done else b'')


def replay(tmp_path, frames, *, enabled=True, done=True, fail_first=False, metrics=None, capture_setup=None, dispatch=False):
    from run_agent import AIAgent
    agent=AIAgent(api_key='local',base_url='http://127.0.0.1:8000/v3',provider='custom',
                  model='local',quiet_mode=True,skip_context_files=True,skip_memory=True)
    agent._current_turn_id='turn-one';agent._current_api_request_id='request-one'
    requests=[]
    class DroppedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield wire([frame(args='{')],False)
            raise httpx.RemoteProtocolError('synthetic drop')
    def respond(request):
        requests.append(request)
        stream=DroppedStream() if fail_first and len(requests)==1 else httpx.ByteStream(wire(frames,done))
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=stream)
    client=OpenAI(api_key='local',base_url='http://127.0.0.1:8000/v3',max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    names=[f['choices'][0]['delta']['tool_calls'][0]['function']['name'] for f in frames]
    kwargs={'model':'local','messages':[{'role':'user','content':'private test input'}],
            'tools':[{'type':'function','function':{'name':name,'parameters':{'type':'object'}}} for name in set(names)]}
    capture=ProtocolCapture(tmp_path/'trace.jsonl',wire=True) if enabled else None
    if capture:agent._tool_protocol_capture=capture
    if capture and capture_setup:capture_setup(capture)
    try:
        with patch.object(agent,'_create_request_openai_client',return_value=client),patch.object(agent,'_close_request_openai_client'):
            response=agent._interruptible_streaming_api_call(kwargs)
        if dispatch:
            from agent.tool_executor import execute_tool_calls_sequential
            invoked=[]
            messages=[]
            agent.valid_tool_names=set(names)
            with patch('run_agent.handle_function_call', side_effect=lambda *a, **k: invoked.append(a) or '{"ok":true}'):
                execute_tool_calls_sequential(agent,response.choices[0].message,messages,'test')
            if metrics is not None:
                metrics.update(executions=len(invoked), messages=[
                    {key: m.get(key) for key in ('role','content','tool_call_id','name','effect_disposition')}
                    for m in messages])
        if metrics is not None:metrics['requests']=len(requests)
        if capture:
            for tc in response.choices[0].message.tool_calls or []:
                execution_event(agent,'argument_validation',tc.id,status='success')
                execution_event(agent,'execution_start',tc.id)
                execution_event(agent,'execution_terminal',tc.id,status='success')
        return response, [] if not capture else [json.loads(x) for x in capture.path.read_text().splitlines()]
    finally:
        if capture:capture.close()
        client.close()


def test_real_sse_sdk_and_assembly_are_correlated(tmp_path):
    response,events=replay(tmp_path,[frame(args='{'),frame(args='}',finish='tool_calls')])
    assert response.choices[0].message.tool_calls[0].function.arguments=='{}'
    assert len([e for e in events if e['event']=='tool_delta' and e['layer']=='wire'])==2
    assert len([e for e in events if e['event']=='tool_delta' and e['layer']=='sdk'])==2
    assembly=next(e for e in events if e['event']=='assembled_arguments')
    execution=next(e for e in events if e['event']=='execution_start')
    assert assembly['valid_object'] is True
    for key in ('turn_id','logical_request_id','attempt_id','tool_index','tool_id'):
        assert assembly[key]==execution[key]
    assert any(e['event']=='wire_done' for e in events)


def test_same_index_different_ids_is_visible_not_hidden(tmp_path):
    response,events=replay(tmp_path,[frame(identity='a'),frame(identity='b',finish='tool_calls')])
    assert len(response.choices[0].message.tool_calls)==2
    slots=[e for e in events if e['event']=='assembled_slot']
    assert [e['raw_index'] for e in slots]==[0,0]
    assert [e['tool_index'] for e in slots]==[0,1]


def test_internal_retry_keeps_request_and_changes_attempt(tmp_path):
    response,events=replay(tmp_path,[frame(finish='tool_calls')],fail_first=True)
    assert response.choices[0].finish_reason=='tool_calls'
    starts=[e for e in events if e['event']=='attempt_start']
    assert len(starts)==2
    assert starts[0]['logical_request_id']==starts[1]['logical_request_id']
    assert starts[0]['attempt_id']!=starts[1]['attempt_id']
    assert any(e['event']=='attempt_error' for e in events)
    assert any(e['event']=='retry_scheduled' for e in events)


def test_many_slots_are_not_misreported_as_many_retries(tmp_path):
    frames=[frame(index=i,identity=f'call_{i}',finish='tool_calls' if i==39 else None) for i in range(40)]
    response,events=replay(tmp_path,frames)
    assert len(response.choices[0].message.tool_calls)==40
    assert len([e for e in events if e['event']=='attempt_start'])==1
    assert len([e for e in events if e['event']=='assembled_arguments'])==40


def test_malformed_missing_finish_preserves_original_failure(tmp_path):
    frames=[frame(name='tool_call',args='{"name": "demo_home_status", "arguments": {"}}}}')]
    response,events=replay(tmp_path,frames,done=False)
    assert response.choices[0].finish_reason!='tool_calls'
    assert any(e['event']=='assembly_end' and e['missing_finish'] for e in events)
    assert any(e['event']=='assembled_arguments' and not e['valid_object'] for e in events)
    assert not any(e['event']=='wire_done' for e in events)


def test_disabled_trace_does_not_change_response(tmp_path):
    enabled=tmp_path/'enabled';enabled.mkdir()
    disabled=tmp_path/'disabled';disabled.mkdir()
    frames=[frame(finish='tool_calls')]
    a,_=replay(enabled,frames)
    b,events=replay(disabled,frames,enabled=False)
    assert a.choices[0].message.tool_calls[0].function.arguments==b.choices[0].message.tool_calls[0].function.arguments
    assert a.choices[0].finish_reason==b.choices[0].finish_reason
    assert not events and not (disabled/'trace.jsonl').exists()


def test_payloads_and_secrets_never_enter_capture(tmp_path):
    secret='sensitive-password-0123456'
    _,events=replay(tmp_path,[frame(args=json.dumps({'token':secret,'recipient':'private@example.com'}),finish='tool_calls')])
    text=(tmp_path/'trace.jsonl').read_text()
    assert secret not in text and 'private@example.com' not in text and 'private test input' not in text
    assert 'token' not in text.replace('first_token','')
    assert (tmp_path/'trace.jsonl').stat().st_mode & 0o777 == 0o600


def test_large_legal_arguments_are_not_truncated_by_wire_capture(tmp_path):
    args=json.dumps({'content':'x'*70000})
    response,events=replay(tmp_path,[frame(args=args,finish='tool_calls')])
    assert response.choices[0].message.tool_calls[0].function.arguments==args
    assert any(e['event']=='wire_event_omitted' for e in events)
    assert next(e for e in events if e['event']=='assembled_arguments')['valid_object']


def test_bounded_capture_and_no_overwrite(tmp_path):
    path=tmp_path/'trace.jsonl'
    with ProtocolCapture(path,max_events=3,max_bytes=1024) as cap:
        for i in range(20):cap.emit('probe',number=i)
        assert cap.count==3 and cap.dropped==17
    assert path.stat().st_size<=1024
    with pytest.raises(FileExistsError):ProtocolCapture(path)


def test_nonlocal_provider_not_captured(tmp_path):
    with ProtocolCapture(tmp_path/'trace.jsonl') as cap:
        assert begin_request(NS(_tool_protocol_capture=cap,base_url='https://example.com'),{}) is None
    assert not (tmp_path/'trace.jsonl').read_text()


def test_closed_diagnostic_cannot_break_requests(tmp_path):
    cap=ProtocolCapture(tmp_path/'trace.jsonl');cap.close()
    agent=NS(_tool_protocol_capture=cap,base_url='http://localhost:8000/v3')
    trace=begin_request(agent,{})
    trace.event('probe')
    execution_event(agent,'execution_start','x')


def test_real_execution_middleware_records_start_and_block(tmp_path):
    from run_agent import AIAgent
    from agent.tool_executor import _run_agent_tool_execution_middleware
    agent=AIAgent(api_key='local',provider='custom',base_url='http://localhost:8000/v3',
                  model='local',quiet_mode=True,skip_context_files=True,skip_memory=True)
    invoked=[]
    with ProtocolCapture(tmp_path/'execution.jsonl') as cap:
        agent._tool_protocol_capture=cap
        begin_request(agent,{})
        call=dict(agent=agent,function_name='diagnostic_probe',function_args={},effective_task_id='test',
                  execute=lambda args:invoked.append(args) or '{"success":true}')
        result=_run_agent_tool_execution_middleware(**call,tool_call_id='first')
        assert result.dispatched and invoked==[{}]
        result=_run_agent_tool_execution_middleware(**call,tool_call_id='second',scope_block='Test scope denial')
        assert result.blocked and invoked==[{}]
    events=[json.loads(x) for x in (tmp_path/'execution.jsonl').read_text().splitlines()]
    assert len([e for e in events if e['event']=='execution_start'])==1
    assert any(e['event']=='execution_terminal' and e['status']=='blocked' for e in events)


def test_attempt_identity_survives_outer_request_reentry(tmp_path):
    with ProtocolCapture(tmp_path/'identity.jsonl') as cap:
        agent=NS(_tool_protocol_capture=cap,base_url='http://localhost:8000',_current_turn_id='t',_current_api_request_id='r')
        first=begin_request(agent,{})
        first.event('attempt_start',attempt_id=1)
        second=begin_request(agent,{})
        second.event('attempt_start',attempt_id=1)
    events=[json.loads(x) for x in (tmp_path/'identity.jsonl').read_text().splitlines()]
    attempts=[e for e in events if e['event']=='attempt_start']
    assert attempts[0]['logical_request_id']==attempts[1]['logical_request_id']
    assert attempts[0]['attempt_id']!=attempts[1]['attempt_id']


def test_actual_dispatch_rejects_malformed_args_without_start(tmp_path):
    from run_agent import AIAgent
    from agent.tool_executor import execute_tool_calls_sequential
    agent=AIAgent(api_key='local',provider='custom',base_url='http://localhost:8000/v3',
                  model='local',quiet_mode=True,skip_context_files=True,skip_memory=True)
    tc=NS(id='malformed',function=NS(name='demo_home_status',arguments='{{'))
    messages=[]
    with ProtocolCapture(tmp_path/'invalid.jsonl') as cap:
        agent._tool_protocol_capture=cap
        begin_request(agent,{})
        execute_tool_calls_sequential(agent,NS(tool_calls=[tc]),messages,'test',finalize=False)
    events=[json.loads(x) for x in (tmp_path/'invalid.jsonl').read_text().splitlines()]
    assert any(e['event']=='argument_validation' and e['status']=='error' for e in events)
    assert not any(e['event']=='execution_start' for e in events)
    assert any(e['event']=='execution_terminal' and e['status']=='error' for e in events)
