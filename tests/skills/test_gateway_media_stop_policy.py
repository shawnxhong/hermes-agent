"""Explicit Feishu stop requests must have a current native tool receipt."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from openai.types.chat import ChatCompletion

spec = importlib.util.spec_from_file_location('gateway_stop_policy',
    Path(__file__).resolve().parents[2] / 'demo-runtime/plugins/gateway/demo-media/__init__.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


@pytest.fixture(autouse=True)
def clean():
    p._stop_turns.clear()
    p._stop_current.clear()


def payload(text='stop music'):
    return {'model': 'test', 'messages': [{'role': 'system', 'content': 'unchanged'},
        {'role': 'assistant', 'content': 'Music stopped.'}, {'role': 'user', 'content': text}],
        'tools': [{'type': 'function', 'function': p.SCHEMA}]}


def arm(text='stop music', session='s', turn='t', platform='feishu'):
    return p.require_stop_tool(request=payload(text), session_id=session, turn_id=turn, platform=platform)


@pytest.mark.parametrize('text', ['stop music', 'Stop video', 'Stop playback again.',
    'Please close the video.', 'Could you stop the music please?', 'stop playing the song'])
def test_explicit_stop_forces_native_tool_without_changing_context_or_tools(text):
    request = payload(text)
    original = deepcopy(request)
    changed = p.require_stop_tool(request=request, platform='feishu', session_id='s', turn_id='t')['request']
    assert changed['tool_choice']['function']['name'] == 'local_media'
    assert changed['messages'] == original['messages'] and changed['tools'] == original['tools']
    assert request == original


@pytest.mark.parametrize('text', ['What does stop music mean?', 'Do not stop music.',
    'Say "stop music".', 'Stop music and email a report.', 'Play a song.', 'Stop',
    'If I stop music, will it restart?', 'Stop the music lesson.'])
def test_unrelated_negated_quoted_or_compound_requests_are_not_forced(text):
    assert arm(text) is None
    assert p.guard_stop_confirmation(response_text='unchanged', platform='feishu', session_id='s') is None


def test_voice_and_missing_identity_remain_unchanged():
    assert arm(platform='cli') is None
    assert arm(session='') is None
    assert arm(turn='') is None


def test_provider_text_only_reply_cannot_confirm_a_stop():
    arm()
    answer = p.guard_stop_confirmation(response_text='Music stopped.', platform='feishu', session_id='s')
    assert answer == p._STOP_UNCONFIRMED


def test_unavailable_media_tool_cannot_report_success():
    request = payload(); request['tools'] = []
    assert p.require_stop_tool(request=request, platform='feishu', session_id='s', turn_id='t') is None
    assert p.guard_stop_confirmation(response_text='Music stopped.', platform='feishu', session_id='s') == p._STOP_UNCONFIRMED


def test_native_tool_arguments_preserve_stop_intent_and_block_other_actions():
    arm()
    assert p.constrain_stop_args(tool_name='local_media', args={'action': 'play', 'target': 'x'},
        session_id='s', turn_id='t')['args'] == {'action': 'stop'}
    assert p.restrict_stop_tools(tool_name='cronjob_manage', session_id='s', turn_id='t')['action'] == 'block'
    assert p.restrict_stop_tools(tool_name='local_media', session_id='s', turn_id='t') is None


def receipt(result, session='s', turn='t', call='c'):
    p.record_stop_receipt(tool_name='local_media', args={'action': 'stop'}, result=result,
        session_id=session, turn_id=turn, tool_call_id=call)


def test_only_durable_current_call_can_finish_successfully():
    arm(); receipt({'success': True, 'action': 'stopped', 'state': 'inactive'})
    assert p.finish_stop_tool(session_id='s', tool_results=[{'tool_call_id': 'other'}]) is None
    assert p.finish_stop_tool(session_id='s', tool_results=[{'tool_call_id': 'c'}])['final_response'] == 'Playback is stopped.'


@pytest.mark.parametrize('result', [None, '{broken', {'success': False, 'error': 'offline'},
    {'success': True}, {'success': True, 'action': 'stopped', 'state': 'active'}])
def test_missing_malformed_failed_or_active_results_never_confirm(result):
    arm(); receipt(result)
    assert p.guard_stop_confirmation(response_text='Music stopped.', platform='feishu', session_id='s') == p._STOP_UNCONFIRMED


def test_receipts_never_leak_across_turns_or_chats():
    arm(); receipt({'success': True, 'action': 'stopped', 'state': 'inactive'})
    arm(session='s', turn='next')
    receipt({'success': True, 'action': 'stopped', 'state': 'inactive'}, turn='t')
    assert p.guard_stop_confirmation(response_text='Music stopped.', platform='feishu', session_id='s') == p._STOP_UNCONFIRMED
    assert p.guard_stop_confirmation(response_text='Music stopped.', platform='feishu', session_id='other') is None


def test_new_ordinary_turn_clears_pending_stop_guard():
    arm(); arm('What is 2+2?', turn='next')
    assert p.guard_stop_confirmation(response_text='4', platform='feishu', session_id='s') is None


def completion(text='', action=None):
    calls = None if action is None else [{'id': 'stop-call', 'type': 'function',
        'function': {'name': 'local_media', 'arguments': json.dumps({'action': action})}}]
    return ChatCompletion.model_validate({'id': 'test', 'created': 1, 'object': 'chat.completion', 'model': 'test',
        'choices': [{'index': 0, 'finish_reason': 'tool_calls' if calls else 'stop',
            'message': {'role': 'assistant', 'content': text, 'tool_calls': calls}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}})


@pytest.fixture
def native(tmp_path, monkeypatch):
    from hermes_cli import plugins
    from hermes_state import SessionDB
    from run_agent import AIAgent
    monkeypatch.setattr('agent.title_generator.maybe_auto_title', lambda *a, **kw: None)
    from hermes_constants import get_hermes_home
    (get_hermes_home()/'config.yaml').write_text('tools:\n  tool_search:\n    enabled: off\n')
    manager = plugins.PluginManager(); monkeypatch.setattr(plugins, '_plugin_manager', manager)
    manager.discover_and_load()
    p.register(plugins.PluginContext(plugins.PluginManifest(name='demo-media'), manager))
    db = SessionDB(tmp_path/'session.db')
    agent = AIAgent(model='test', provider='custom', base_url='http://127.0.0.1:1/v1', api_key='test',
        api_mode='chat_completions', quiet_mode=True, max_iterations=4, platform='feishu',
        enabled_toolsets=['local_media'], skip_memory=True, skip_context_files=True,
        skip_background_review=True, session_db=db, session_id='stop-native')
    agent.client = Mock(spec=['chat', 'is_closed', 'close']); agent.client.is_closed = False
    return agent, db


def test_actual_native_loop_rejects_provider_text_only_false_success(native, monkeypatch):
    agent, db = native
    bridge = Mock(); monkeypatch.setattr('tools.playback_control.request', bridge)
    agent.client.chat.completions.create = Mock(return_value=completion('Music stopped.'))
    result = agent.run_conversation('stop music')
    assert result['final_response'] == p._STOP_UNCONFIRMED
    bridge.assert_not_called()
    assert db.get_messages(agent.session_id)[-1]['content'] == p._STOP_UNCONFIRMED


@pytest.mark.parametrize('success', [True, False])
def test_actual_native_executor_stops_once_and_finalizes_from_result(native, monkeypatch, success):
    agent, db = native
    bridge = Mock(return_value={'success': success, 'action': 'stopped', 'state': 'inactive'})
    monkeypatch.setattr('tools.playback_control.request', bridge)
    agent.client.chat.completions.create = Mock(return_value=completion(action='play'))
    result = agent.run_conversation('stop music')
    assert result['final_response'] == ('Playback is stopped.' if success else p._STOP_UNCONFIRMED)
    bridge.assert_called_once_with('stop', '')
    assert agent.client.chat.completions.create.call_count == 1
    assert result['turn_exit_reason'] == 'plugin_tool_completion'
    messages = db.get_messages(agent.session_id)
    assert [m['role'] for m in messages] == ['user', 'assistant', 'tool', 'assistant']
    assert messages[-1]['content'] == result['final_response']


def test_repeated_native_stop_calls_again_despite_previous_success(native, monkeypatch):
    agent, db = native
    bridge = Mock(return_value={'success': True, 'action': 'stopped', 'state': 'inactive'})
    monkeypatch.setattr('tools.playback_control.request', bridge)
    agent.client.chat.completions.create = Mock(return_value=completion(action='stop'))
    first = agent.run_conversation('stop music')
    second = agent.run_conversation('stop music', conversation_history=first['messages'])
    assert second['final_response'] == 'Playback is stopped.'
    assert bridge.call_count == 2
    assert agent.client.chat.completions.create.call_count == 2
    assert sum(m['role'] == 'tool' for m in db.get_messages(agent.session_id)) == 2
