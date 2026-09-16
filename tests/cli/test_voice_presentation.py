from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from hermes_cli import voice_presentation as present, general_voice as base, voice_outbox
from hermes_cli.voice_continuity_store import ContinuityStore


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(base, 'config', lambda: {'enabled': True, 'default_recipient': 'test@example.com'})
    sender = Mock(return_value={'success': True})
    monkeypatch.setattr(voice_outbox, 'kick', lambda: voice_outbox.drain(sender=sender))
    summary = Mock(return_value='I completed the work and checked the result.')
    monkeypatch.setattr(base, '_summary', summary)
    token = present.turn_presentation.set({'handled': False, 'decision': {'detail': True}})
    yield NS(_interrupt_requested=False), sender, summary
    present.turn_presentation.reset(token)


def result(body='I completed the work.', **kwargs):
    return dict(final_response=body, completed=True, failed=False, api_calls=2, messages=[], **kwargs)


def test_three_sentence_natural_reply_is_identity(rig):
    text="I'm doing well. I'm ready to help. What would you like to do?"
    assert present.spoken_body(rig[0], text, 'How are you?') == (text, 0)
    rig[2].assert_not_called()


def test_nonvoice_and_already_presented_unchanged(rig):
    original=result()
    for state in (None, {'handled':True}):
        token=present.turn_presentation.set(state)
        try: assert present.finish_native(rig[0], original, 'Do work', 's') is original
        finally: present.turn_presentation.reset(token)
    rig[1].assert_not_called();rig[2].assert_not_called()


def test_long_native_prose_compressed_once_and_original_emailed(rig):
    body='I checked the configuration and found a useful improvement. '*20
    value=present.finish_native(rig[0], result(body), 'Audit the configuration', 's')
    assert value['completed'] and value['api_calls']==3
    assert value['final_response'].startswith('I completed')
    rig[2].assert_called_once()
    rig[1].assert_called_once_with('test@example.com', body)


@pytest.mark.parametrize('flag', ['failed', 'partial', 'interrupted'])
def test_unsuccessful_native_work_not_emailed(rig, flag):
    original=result();original[flag]=True
    present.finish_native(rig[0], original, 'Do work', 's')
    rig[1].assert_not_called()


@pytest.mark.parametrize('body,messages',[
    ('```python\nprint(1)\n```', []),
    ('import os\nprint(os.getcwd())', []),
    ('raw log line '*30, [{'role':'tool','content':'raw log line '*30}]),
])
def test_source_and_raw_tools_not_auto_exported(rig, body, messages):
    original=result(body);original['messages']=messages
    value=present.finish_native(rig[0], original, 'Do work', 's')
    assert 'not emailed' in value['final_response']
    rig[1].assert_not_called()


def test_explicit_no_email_and_native_mail_no_duplicate(rig):
    present.finish_native(rig[0], result(), "Do work but don't email it", 's')
    rig[1].assert_not_called()
    present.turn_presentation.get()['handled']=False
    original=result();original['messages']=[{'role':'user','content':'send'},
        {'role':'assistant','tool_calls':[{'function':{'name':'send_message','arguments':'{}'}}]}]
    present.finish_native(rig[0], original, 'Send a message', 's')
    rig[1].assert_not_called()


def test_presentation_storage_failure_does_not_fail_native_work(rig, monkeypatch):
    monkeypatch.setattr(ContinuityStore, 'start', Mock(side_effect=RuntimeError('disk full')))
    value=present.finish_native(rig[0], result(), 'Do work', 's')
    assert value['completed'] and not value['failed']
    rig[1].assert_not_called()


def test_interrupt_during_compression_does_not_save_or_send(rig):
    def interrupted(*args, **kwargs):
        rig[0]._interrupt_requested=True
        return 'Finished.'
    rig[2].side_effect=interrupted
    original=result('A long result. '*40)
    assert present.finish_native(rig[0], original, 'Do work', 's') is original
    assert ContinuityStore().current('s') is None
    rig[1].assert_not_called()


def test_summary_allows_natural_delivery_words_and_three_sentences():
    base._validate_summary('The network is working. I sent the requested command. It completed successfully.')
    assert not present.is_brief('| A | B |\n|---|---|\n| 1 | 2 |')


def test_short_native_reply_not_detailed_no_email(rig):
    present.turn_presentation.get()['decision']['detail']=False
    assert present.finish_native(rig[0], result(), 'Do work', 's')['final_response']=='I completed the work.'
    rig[1].assert_not_called();rig[2].assert_not_called()
