"""Regression probes from the stage-one review; actual SDK and dispatch paths."""
import json
import threading
from types import SimpleNamespace as NS

import httpx
import pytest
from openai import OpenAI

from agent.tool_protocol_diag import ProtocolCapture, begin_request, execution_event


@pytest.mark.parametrize('mode', ['sequential', 'concurrent'])
@pytest.mark.parametrize('outcome', ['ok', 'error', 'exception', 'blocked', 'timeout', 'cancelled', 'cancel_inflight'])
def test_real_dispatch_terminal(tmp_path, monkeypatch, mode, outcome):
    from run_agent import AIAgent
    from tools.registry import registry
    import agent.tool_executor as executor
    agent = AIAgent(api_key='local', provider='custom', base_url='http://localhost:8000',
                    model='local', quiet_mode=True, skip_memory=True, skip_context_files=True)
    name = 'diagnostic_review_probe'
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    invoked = []
    futures = []
    import tools.daemon_pool as pool
    class TrackedPool(pool.DaemonThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            futures.append(future)
            if outcome in {'timeout', 'cancel_inflight'}:
                assert entered.wait(10), 'worker must enter handler before test deadline'
            return future
    monkeypatch.setattr(pool, 'DaemonThreadPoolExecutor', TrackedPool)
    def handler(args, **kwargs):
        invoked.append(args)
        entered.set()
        try:
            if outcome == 'cancel_inflight':
                agent._interrupt_requested = True
            if outcome in {'timeout', 'cancel_inflight'}:
                assert release.wait(10)
            if outcome == 'exception':
                raise ValueError('synthetic failure')
            return json.dumps({'error': 'synthetic error'} if outcome == 'error' else {'ok': True})
        finally:
            finished.set()
    # Real registry/handler and executor; replace only the external policy boundary.
    monkeypatch.setitem(registry._tools, name, None)
    registry._tools.pop(name)
    registry.register(name, 'diagnostic_test', {'name': name, 'parameters': {'type': 'object'}}, handler)
    agent.valid_tool_names = {name}
    if outcome == 'blocked':
        monkeypatch.setattr('hermes_cli.plugins._dispatch_pre_tool_call_hooks', lambda *a, **k: ('test denial', None))
    if outcome == 'cancelled':
        agent._interrupt_requested = True
    if outcome == 'timeout':
        monkeypatch.setenv('HERMES_CONCURRENT_TOOL_TIMEOUT_S', '1.0')
    cap, _ = traced(tmp_path)
    agent._tool_protocol_capture = cap
    agent._current_turn_id, agent._current_api_request_id = 'T1', 'R1'
    call = NS(id='C1', type='function', function=NS(name=name, arguments='{}'))
    messages = []
    with cap:
        trace = begin_request(agent, {})
        trace.slot(1, 0, 0, 'C1')
        trace.bind_call(call, 1, 0)
        # Reuse the ID in a newer request; frozen identity must win.
        agent._current_turn_id, agent._current_api_request_id = 'T2', 'R2'
        begin_request(agent, {}).slot(1, 0, 0, 'C1')
        try:
            getattr(executor, 'execute_tool_calls_' + mode)(agent, NS(tool_calls=[call]), messages, 'test')
        finally:
            release.set()
            if entered.is_set():
                assert finished.wait(10)
            for future in futures:
                if not future.cancelled():
                    future.result(timeout=10)
    events = rows(cap.path)
    terminals = [e for e in events if e['event'] == 'execution_terminal']
    starts = [e for e in events if e['event'] == 'execution_start']
    assert len(terminals) == 1
    expected = {'exception': 'error', 'cancel_inflight': 'cancelled'}.get(outcome, outcome)
    assert terminals[0]['status'] == expected
    assert terminals[0]['logical_request_id'] == trace.request_id
    assert terminals[0]['turn_id'] == trace.turn_id
    assert len(starts) == len(invoked) == (0 if outcome in {'blocked', 'cancelled'} else 1)
    if starts:
        assert starts[0]['execution_id'] == terminals[0]['execution_id']


def test_reused_or_missing_id_is_explicitly_unknown(tmp_path):
    cap, agent = traced(tmp_path)
    with cap:
        begin_request(agent, {}).slot(1, 0, 0, 'reused')
        agent._current_api_request_id = 'R2'
        begin_request(agent, {}).slot(1, 0, 0, 'reused')
        for identity in ('reused', 'missing'):
            execution_event(agent, 'execution_terminal', identity, status='ok')
    for event in rows(cap.path)[-2:]:
        assert event['correlation'] == 'unknown'
        assert event['logical_request_id'] is event['turn_id'] is event['attempt_id'] is None


@pytest.mark.parametrize('fault', ['none', 'write', 'flush', 'assembled', 'chunk', 'slot', 'wrap_response', 'bind_call', 'event'])
@pytest.mark.parametrize('drop', [False, True])
def test_diagnostic_faults_do_not_change_stream_retry_or_dispatch(tmp_path, monkeypatch, fault, drop):
    from tests.agent.test_tool_protocol_diag import replay, frame
    from agent.tool_protocol_diag import RequestTrace
    frames = [frame(name='diagnostic_probe', args='{"value":"\ud800"}', finish='tool_calls')]
    baseline = tmp_path / 'off'
    baseline.mkdir()
    before = {}
    a, _ = replay(baseline, frames, enabled=False, fail_first=drop, dispatch=True, metrics=before)
    def fail(*args, **kwargs):
        raise OSError('synthetic diagnostic failure')
    if fault not in {'none', 'write', 'flush'}:
        monkeypatch.setattr(RequestTrace, fault, fail)
    def setup(cap):
        if fault in {'write', 'flush'}:
            monkeypatch.setattr(cap._file, fault, fail)
    after = {}
    enabled = tmp_path / 'on'
    enabled.mkdir()
    b, _ = replay(enabled, frames, fail_first=drop, capture_setup=setup, dispatch=True, metrics=after)
    assert before == after
    assert after['requests'] == (2 if drop else 1)
    assert after['executions'] == 1
    assert a.choices[0].finish_reason == b.choices[0].finish_reason
    assert a.choices[0].message.tool_calls[0].function.arguments == b.choices[0].message.tool_calls[0].function.arguments


def test_wire_passthrough_and_oversize_recovery(tmp_path):
    cap, agent = traced(tmp_path)
    chunks = [b'data: '+b'x'*70000+b'\r', b'', b'\n\r', b'', b'\n',
              b'data: {"choices":[]}\r', b'', b'\n\r\n', b'data: [DONE]\r\n\r\n']
    class Bytes(httpx.SyncByteStream):
        def __iter__(self):
            yield from chunks
    with cap:
        response = httpx.Response(200, stream=Bytes())
        begin_request(agent, {}).wrap_response(response, 1)
        assert list(response.stream) == chunks
    events = rows(cap.path)
    assert sum(e['event'] == 'wire_event_omitted' for e in events) == 1
    assert any(e['event'] == 'wire_done' for e in events)
    assert not any(e['event'] == 'wire_invalid_json' for e in events)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def traced(tmp_path):
    cap = ProtocolCapture(tmp_path / 'review.jsonl', wire=True)
    agent = NS(_tool_protocol_capture=cap, base_url='http://localhost:8000',
               _current_turn_id='T1', _current_api_request_id='R1')
    return cap, agent


def test_surrogate_arguments_do_not_escape_diagnostics(tmp_path):
    cap, agent = traced(tmp_path)
    with cap:
        trace = begin_request(agent, {})
        original = '{"value":"\ud800"}'
        calls = {0: {'id': 'C1', 'function': {'arguments': original}}}
        trace.assembled(1, calls, 'tool_calls')
        assert calls[0]['function']['arguments'] == original
    assert next(r for r in rows(cap.path) if r['event'] == 'assembled_arguments')['valid_object']


@pytest.mark.parametrize('new_turn', [False, True])
def test_late_execution_retains_original_request(tmp_path, new_turn):
    cap, agent = traced(tmp_path)
    with cap:
        first = begin_request(agent, {})
        first.slot(1, 0, 0, 'C1')
        agent._current_api_request_id = 'R2'
        if new_turn:
            agent._current_turn_id = 'T2'
        begin_request(agent, {})
        execution_event(agent, 'execution_terminal', 'C1', status='success')
    event = rows(cap.path)[-1]
    assert event['logical_request_id'] == first.request_id
    assert event['turn_id'] == first.turn_id
    assert event['attempt_id'] == first.invocation_id + ':1'


@pytest.mark.parametrize('separator', [b'\n', b'\r\n', b'\r'])
def test_multiline_sse_is_not_invalid_json(tmp_path, separator):
    cap, agent = traced(tmp_path)
    # A single valid JSON event split across multiple SSE data fields.
    lines = [b'data: {"id":"c","object":"chat.completion.chunk","created":0,"model":"local",',
             b'data: "choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":"stop"}]}',
             b'', b'data: [DONE]', b'', b'']
    data = separator.join(lines)
    class Bytes(httpx.SyncByteStream):
        def __iter__(self):
            for b in data:
                yield bytes([b])  # Network boundaries split CRLF and fields.
    with cap:
        trace = begin_request(agent, {})
        def handler(request):
            response = httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=Bytes())
            trace.wrap_response(response, 1)
            return response
        with OpenAI(api_key='test', base_url=agent.base_url,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
            chunks = list(client.chat.completions.create(model='local', messages=[], stream=True))
    assert chunks[0].choices[0].delta.content == 'hello'
    assert not any(e['event'] == 'wire_invalid_json' for e in rows(cap.path))
    assert any(e['event'] == 'wire_done' for e in rows(cap.path))


@pytest.mark.parametrize('result,expected', [('{"ok":true}', 'ok'), ('{"error":"test failure"}', 'error')])
def test_terminal_uses_existing_result_status(tmp_path, result, expected):
    from agent.tool_executor import _emit_terminal_post_tool_call
    cap, agent = traced(tmp_path)
    with cap:
        _emit_terminal_post_tool_call(agent, function_name='diagnostic_probe', function_args={},
                                     result=result, effective_task_id='test', tool_call_id='C1')
    assert rows(cap.path)[-1]['status'] == expected
