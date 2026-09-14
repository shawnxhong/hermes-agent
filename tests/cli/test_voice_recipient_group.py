from unittest.mock import Mock
import pytest
from hermes_cli import general_voice as voice
from hermes_cli.voice_delivery import email_recipients, valid_email_recipients
from hermes_cli.voice_continuity import _address
from hermes_cli.voice_continuity_store import ContinuityStore

GROUP = 'one@example.com, two@example.com, three@example.com'


def test_group_parsing_and_duplicates():
    assert email_recipients('one@example.com, ONE@example.com, two@example.com') == ['one@example.com','two@example.com']
    assert valid_email_recipients(GROUP)


@pytest.mark.parametrize('value', ['', None, [], 'one@example.com,', 'one@example.com, invalid',
                                  'one@example.com\nBcc: evil@example.com'])
def test_invalid_group_rejected(value):
    assert not valid_email_recipients(value)


def test_default_group_does_not_override_explicit_address():
    assert _address('Email me the plan.', GROUP, delivery_requested=True) == (GROUP, False)
    assert _address('Email it to other@example.com.', GROUP, delivery_requested=True) == ('other@example.com', False)
    assert _address('My email address is one.example.com', GROUP, delivery_requested=True) == ('one@example.com', True)


def test_all_group_members_sent_once_and_no_blind_retry_on_partial_failure(monkeypatch):
    from tools import send_message_tool
    transport = Mock(side_effect=[{'success':True},{'success':False},{'success':True}])
    monkeypatch.setattr(send_message_tool, 'send_message_tool', transport)
    store = ContinuityStore()
    task = store.start('s', 'Write a plan')
    task, version = store.save_result('s', task, body='Full plan', summary='Plan')
    for _ in range(2):
        assert store.submit_result('s', task, version, GROUP, sender=voice._send) == 'unconfirmed'
    assert [c.args[0]['target'] for c in transport.call_args_list] == [
        'email:one@example.com','email:two@example.com','email:three@example.com']
    assert all(c.args[0]['message']=='Full plan' for c in transport.call_args_list)


def test_group_success_and_explicit_single_stays_single(monkeypatch):
    from tools import send_message_tool
    transport = Mock(return_value={'success': True})
    monkeypatch.setattr(send_message_tool, 'send_message_tool', transport)
    assert voice._send(GROUP, 'Full plan')['success']
    assert transport.call_count == 3
    transport.reset_mock()
    assert voice._send('other@example.com', 'Full plan')['success']
    transport.assert_called_once_with({'action':'send','target':'email:other@example.com','message':'Full plan'})
