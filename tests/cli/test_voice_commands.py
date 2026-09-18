import pytest
from hermes_cli.voice_commands import is_clear_command


@pytest.mark.parametrize('text', [
    'clear memory', 'Clear Memory.', ' CLEAR   CONTEXT! ',
    'clear conversation', '清空上下文。', '清除上下文', '清空对话！',
    'please clear up your memory', 'Please, clear up your memory.',
    'clear memory please', 'clear your memory, please!',
    'Could you please clear your memory?', 'Can you clear the context?',
    'Would you clear this conversation for me, please?',
    'please clear our current conversation', 'clear up memory',
    'clear the current context', '请清空上下文。',
])
def test_exact_command(text):
    assert is_clear_command(text)


@pytest.mark.parametrize('text', [
    None, '', 'memory', 'clear', 'clear memories',
    'What does clear memory mean?', "Don't clear memory", '不要清空上下文',
    'Tell me how to clear context', 'clear memory and send an email',
    '"clear memory"', '清空上下文是什么意思',
    'please do not clear up your memory', "Could you please not clear memory?",
    'please explain how to clear up your memory',
    'What happens if I say please clear up your memory?',
    'Can you tell me how to clear memory?', 'Can you clear memory or not?',
    'please clear memory and send an email', 'please clear my long term memory',
    'please clear your memory of my address', 'clear browser memory',
    'I said please clear up your memory', '"please clear up your memory"',
    '请不要清空上下文', 'please clear context after you send the email',
])
def test_not_a_command(text):
    assert not is_clear_command(text)


def test_endpoint_is_removed_before_matching(monkeypatch):
    from tools import voice_endpoint
    monkeypatch.setattr(voice_endpoint, 'settings', lambda: {'phrase': 'thank you'})
    assert is_clear_command(voice_endpoint.strip_end_phrase('Clear memory. Thank you!'))
    assert not is_clear_command(voice_endpoint.strip_end_phrase("Don't clear memory. Thank you."))
    assert is_clear_command(voice_endpoint.strip_end_phrase('Please clear up your memory. Thank you.'))


def test_polite_request_routes_to_local_clear_once():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from hermes_cli.voice_commands import route_control
    controller = SimpleNamespace(request_clear=Mock())
    cli = SimpleNamespace(_scene_controller=controller)
    assert route_control(cli, 'please clear up your memory')
    controller.request_clear.assert_called_once_with()
    assert not route_control(cli, 'please do not clear up your memory')
    controller.request_clear.assert_called_once_with()
