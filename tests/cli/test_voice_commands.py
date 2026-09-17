import pytest
from hermes_cli.voice_commands import is_clear_command


@pytest.mark.parametrize('text', [
    'clear memory', 'Clear Memory.', ' CLEAR   CONTEXT! ',
    'clear conversation', '清空上下文。', '清除上下文', '清空对话！',
])
def test_exact_command(text):
    assert is_clear_command(text)


@pytest.mark.parametrize('text', [
    None, '', 'memory', 'clear', 'clear memories',
    'What does clear memory mean?', "Don't clear memory", '不要清空上下文',
    'Tell me how to clear context', 'clear memory and send an email',
    '"clear memory"', 'clear memory please', '清空上下文是什么意思',
])
def test_not_a_command(text):
    assert not is_clear_command(text)


def test_endpoint_is_removed_before_matching(monkeypatch):
    from tools import voice_endpoint
    monkeypatch.setattr(voice_endpoint, 'settings', lambda: {'phrase': 'thank you'})
    assert is_clear_command(voice_endpoint.strip_end_phrase('Clear memory. Thank you!'))
    assert not is_clear_command(voice_endpoint.strip_end_phrase("Don't clear memory. Thank you."))
