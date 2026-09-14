import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('demo_media_plugin', ROOT / 'scripts/local-ovms/plugins/demo-media/__init__.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


@pytest.fixture(autouse=True)
def clear_state():
    p._recent.clear()
    p._completed.clear()


def test_play_finishes_without_followup_model_call(monkeypatch):
    select = Mock(return_value=('mp4', ''))
    execute = Mock(return_value={'success': True, 'state': 'active'})
    monkeypatch.setattr(p, 'select_action', select)
    monkeypatch.setattr(p, 'execute', execute)
    agent = SimpleNamespace(_interrupt_requested=False)
    result = p.workflow(agent=agent, user_message='play the mp4 video', session_id='one')
    assert result['handled'] and not result['failed'] and result['api_calls'] == 1
    select.assert_called_once()
    execute.assert_called_once_with('mp4', '')


@pytest.mark.parametrize('text', ['What is MP4?', 'Plan a trip to Boston.', 'What is 2+3?', 'Stop'])
def test_unrelated_tasks_untouched(monkeypatch, text):
    selector = Mock()
    monkeypatch.setattr(p, 'select_action', selector)
    assert p.workflow(agent=None, user_message=text, session_id='one') is None
    selector.assert_not_called()


def test_declined_compound_or_named_request_does_not_execute(monkeypatch):
    monkeypatch.setattr(p, 'select_action', lambda *a: ('none', ''))
    execute = Mock()
    monkeypatch.setattr(p, 'execute', execute)
    assert p.workflow(agent=None, user_message='Play the video and write an email.', session_id='one') is None
    execute.assert_not_called()


@pytest.mark.parametrize('text', ['Do not play the video.',
                                'Play the video and email a report.', '播放视频并发邮件',
                                'Play https://example.com/movie.mp4'])
def test_explicit_scope_exclusions_do_not_ask_model(monkeypatch, text):
    selector = Mock(return_value='mp4')
    monkeypatch.setattr(p, 'select_action', selector)
    assert p.workflow(agent=None, user_message=text, session_id='one') is None
    selector.assert_not_called()


def test_failure_truth_and_cancel(monkeypatch):
    monkeypatch.setattr(p, 'select_action', lambda *a: ('mp4', ''))
    execute = Mock(return_value={'success': False})
    monkeypatch.setattr(p, 'execute', execute)
    assert p.workflow(agent=SimpleNamespace(_interrupt_requested=False), user_message='播放视频', session_id='one')['failed']
    execute.reset_mock()
    p.workflow(agent=SimpleNamespace(_interrupt_requested=True), user_message='播放视频', session_id='one')
    execute.assert_not_called()


def test_gui_guard_never_leaks_across_turns_or_sessions():
    p.after_tool(session_id='one', turn_id='t1', tool_name='local_media', result=json.dumps({'workflow_complete': True}))
    assert p.before_tool(session_id='one', turn_id='t1', tool_name='computer_use')['action'] == 'block'
    assert p.before_tool(session_id='one', turn_id='t2', tool_name='computer_use') is None
    assert p.before_tool(session_id='two', turn_id='t1', tool_name='computer_use') is None
    assert p.before_tool(session_id='one', turn_id='t1', tool_name='terminal') is None


def test_single_selection_uses_only_media_schema():
    agent = SimpleNamespace(model='local', client=Mock())
    agent.client.with_options.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[SimpleNamespace(function=SimpleNamespace(name='local_media', arguments='{"action":"mp4"}'))]))])
    assert p.select_action(agent, 'play MP4', False) == ('mp4', '')
    kwargs = agent.client.with_options.return_value.chat.completions.create.call_args.kwargs
    assert len(kwargs['tools']) == 1
    assert kwargs['tools'][0]['function']['name'] == 'local_media'


def test_list_then_name_and_unrelated_turn(monkeypatch):
    select = Mock(side_effect=[('list', ''), ('play', 'dragonfly')])
    execute = Mock(side_effect=[{'success': True, 'files': [{'name': 'Dragonfly Pro.mp4'}]},
                               {'success': True, 'file': '/Desktop/Dragonfly Pro.mp4'}])
    monkeypatch.setattr(p, 'select_action', select)
    monkeypatch.setattr(p, 'execute', execute)
    agent = SimpleNamespace(_interrupt_requested=False)
    assert p.workflow(agent=agent, user_message='ls Desktop', session_id='s')['handled']
    assert p.workflow(agent=agent, user_message='dragonfly', session_id='s')['handled']
    execute.assert_called_with('play', 'dragonfly')
    assert p.workflow(agent=agent, user_message='What is the capital of France?', session_id='s') is None
    assert p.workflow(agent=agent, user_message='Stop', session_id='s') is None


def test_named_request_reaches_selector(monkeypatch):
    monkeypatch.setattr(p, 'select_action', lambda *a: ('play', 'Avatar.mp4'))
    execute = Mock(return_value={'success': False, 'error': 'not_found'})
    monkeypatch.setattr(p, 'execute', execute)
    result = p.workflow(agent=SimpleNamespace(_interrupt_requested=False),
                        user_message='Play Avatar.mp4', session_id='s')
    execute.assert_called_once_with('play', 'Avatar.mp4')
    assert 'Nothing was started' in result['final_response']


def test_selector_cannot_invent_replacement_filename():
    agent = SimpleNamespace(model='local', client=Mock())
    agent.client.with_options.return_value.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[SimpleNamespace(function=SimpleNamespace(name='local_media', arguments='{"action":"play","target":"demo.mp4"}'))]))])
    with pytest.raises(ValueError, match='user request'):
        p.select_action(agent, 'play Avatar.mp4', False)
