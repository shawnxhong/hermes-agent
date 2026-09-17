import importlib.util
import json
from pathlib import Path
import queue
import threading
from types import SimpleNamespace as NS
from unittest.mock import patch

import httpx
import pytest
from openai import OpenAI

from hermes_cli.native_voice import NativeSpeechDelivery, spoken_text


def delivery():
    q = queue.Queue()
    return NativeSpeechDelivery(q, threading.Event()), q


def test_first_sentence_before_end_and_no_final_replay():
    speech, q = delivery()
    speech.on_stream_start('r1')
    speech('Hello. ')
    assert q.qsize() == 1
    speech('How can I help?')
    speech.on_stream_end(final_text='Hello. How can I help?', finished=True)
    speech.finish('Hello. How can I help?')
    assert speech.committed == ['Hello.', 'How can I help?']
    assert q.qsize() == 2


def test_retry_prefix_and_new_tool_iteration():
    speech, q = delivery()
    speech.on_stream_start('r1')
    speech('I will check. An unfinished')
    speech.on_stream_end(final_text='', finished=False)
    speech.on_stream_start('r1')
    speech('I will check. ')
    speech.on_stream_end(final_text='I will check.', finished=True)
    assert q.qsize() == 1
    speech.on_stream_start('r2')
    speech('Both bedroom air conditioners are off. ')
    speech.on_stream_end(final_text='Both bedroom air conditioners are off.', finished=True)
    speech.finish('Both bedroom air conditioners are off.')
    assert len(speech.committed) == 2


def test_changed_retry_and_cancel_do_not_repeat():
    speech, q = delivery()
    speech.on_stream_start('r1')
    speech('First sentence. ')
    speech.on_stream_end(final_text='', finished=False)
    speech.on_stream_start('r1')
    speech('Different sentence. ')
    speech.on_stream_end(final_text='Different sentence.', finished=True)
    assert q.qsize() == 1 and speech.diverged
    speech.stop.set()
    speech.on_stream_start('r2')
    speech('Late sentence. ')
    speech.finish('Late sentence.')
    assert q.qsize() == 1


def test_code_fence_chunks_and_no_hard_answer_cap():
    speech, q = delivery()
    speech.on_stream_start('r1')
    speech('Here is the code. ```python\nsecret_code()\n')
    speech('```\nDone. ')
    speech.on_stream_end(final_text='Here is the code. ```python\nsecret_code()\n```\nDone.', finished=True)
    assert speech.committed == ['Here is the code.', 'Done.']
    long = ' '.join(['A sentence.'] * 10)
    assert spoken_text(long) == long


@pytest.mark.parametrize('name,tool_count', [('general-voice',0),('travel-voice',0),('demo-home',2),('demo-media',1)])
def test_plugins_are_capabilities_not_extra_model_workflows(name, tool_count):
    path = Path(__file__).resolve().parents[2] / 'scripts/local-ovms/plugins' / name / '__init__.py'
    spec = importlib.util.spec_from_file_location('test_native_'+name.replace('-','_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hooks, prompts, tools = [], [], []
    module.register(NS(get_config=lambda key, default: default,
                       register_hook=lambda *a, **k: hooks.append(a),
                       register_system_prompt_section=lambda *a, **k: prompts.append(a),
                       register_tool=lambda **kw: tools.append(kw)))
    assert not hooks and not prompts and len(tools) == tool_count


def test_real_sdk_native_callback_speaks_before_stream_end():
    from run_agent import AIAgent
    speech, q = delivery()
    def event(delta, finish=None):
        return b'data: '+json.dumps({'id':'c','object':'chat.completion.chunk','created':0,'model':'local',
            'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}).encode()+b'\n\n'
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield event({'content':'<think>private reasoning</think>Hello. '})
            assert q.qsize() == 1, 'first sentence must arrive before stream completion'
            yield event({'tool_calls':[{'index':0,'id':'t','type':'function',
                'function':{'name':'diagnostic','arguments':'{"secret":"DO NOT SPEAK"}'}}]}, 'tool_calls')
            yield b'data: [DONE]\n\n'
    client = OpenAI(api_key='local', base_url='http://localhost:8000', max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,
            headers={'content-type':'text/event-stream'}, stream=Stream()))))
    agent = AIAgent(api_key='local', provider='custom', base_url='http://localhost:8000',
                    model='local', quiet_mode=True, skip_memory=True, skip_context_files=True)
    agent._stream_callback = speech
    with client, patch.object(agent, '_create_request_openai_client', return_value=client), patch.object(agent, '_close_request_openai_client'):
        response = agent._interruptible_streaming_api_call({'model':'local','messages':[]})
    assert response.choices[0].finish_reason == 'tool_calls'
    assert speech.committed == ['Hello.']


def test_voice_marker_has_no_workflow_policy():
    from hermes_cli.voice_response_policy import build_voice_turn_prefix
    assert build_voice_turn_prefix() == build_voice_turn_prefix(followup_enabled=True) == '[Voice input] '
