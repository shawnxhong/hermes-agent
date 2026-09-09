import pytest
from unittest.mock import Mock
from hermes_cli.voice_continuity_store import ContinuityStore
from hermes_cli.voice_delivery import TaskStore, StaleTask


def saved(store,session='s',request='Recommend four restaurants'):
    task=store.start(session,request)
    return store.save_result(session,task,body='Four named restaurants and sources.',summary='Four restaurants.')


def test_simple_content_saved_without_delivery_and_owned_on_return():
    store=ContinuityStore();task,version=saved(store)
    second=store.start('s','Plan a workshop')
    assert store.result('s',task['id'],version)['body'].startswith('Four')
    with pytest.raises(StaleTask):store.result('other',task['id'],version)
    restored=store.select('s',task['id'])
    assert restored['revision']!=task['revision']
    assert len(store.topics('s'))==2
    with pytest.raises(StaleTask):store.save_result('s',task,body='stale',summary='stale')


def test_explanation_does_not_replace_main_revision():
    store=ContinuityStore();task,first=saved(store)
    task,explanation=store.save_result('s',task,body='Because these fit.',summary='They fit.',kind='explanation',parent_version=first)
    assert task['artifact_version']==first and explanation!=first
    task,update=store.save_result('s',task,body='Updated four restaurants.',summary='Updated list.',kind='revision',parent_version=first)
    assert task['artifact_version']==update
    assert store.result('s',task['id'],first)['body']=='Four named restaurants and sources.'


def test_confirmation_consumed_once_and_delivery_is_exact_and_deduped():
    store=ContinuityStore();task,version=saved(store)
    identity=store.pend('s',task,'confirm_recipient',{'version':version,'recipient':'one@example.com'})
    value=store.consume('s',identity)
    assert value['version']==version and store.pending('s') is None
    with pytest.raises(StaleTask):store.consume('s',identity)
    sender=Mock(return_value={'success':True})
    for _ in range(2):assert store.submit_result('s',task,version,value['recipient'],sender=sender)=='accepted'
    sender.assert_called_once_with('one@example.com','Four named restaurants and sources.')
    assert 'recipient' not in store.current('s')


def test_switch_cancels_confirmation_and_old_worker_even_after_return():
    store=ContinuityStore();task,version=saved(store)
    identity=store.pend('s',task,'confirm_recipient',{'version':version})
    store.start('s','A different task')
    store.select('s',task['id'])
    assert store.pending('s') is None
    with pytest.raises(StaleTask):store.consume('s',identity)
    sender=Mock()
    with pytest.raises(StaleTask):store.submit_result('s',task,version,'a@example.com',sender=sender)
    sender.assert_not_called()


def test_unclear_answer_repeats_once_then_pauses():
    store=ContinuityStore();task,version=saved(store)
    identity=store.pend('s',task,'recipient',{'version':version})
    assert not store.unclear('s',identity)
    assert store.pending('s')
    assert store.unclear('s',identity)
    assert store.pending('s') is None
    assert store.current('s')['id']==task['id']


def test_resume_migrates_only_proven_legacy_current_task():
    legacy=TaskStore();lost=legacy.start('s','Old orphan');current=legacy.start('s','Current task')
    store=ContinuityStore()
    assert store.topics('s')[0]['id']==current['id']
    with pytest.raises(StaleTask):store.select('s',lost['id'])
    assert ContinuityStore().current('s')['id']==current['id']


def test_uncertain_send_never_automatically_retried_and_result_retained():
    store=ContinuityStore();task,version=saved(store)
    sender=Mock(side_effect=OSError('unknown SMTP outcome'))
    for _ in range(2):assert store.submit_result('s',task,version,'a@example.com',sender=sender)=='unconfirmed'
    sender.assert_called_once()
    assert store.result('s',task['id'],version)


def test_index_and_recent_turns_are_bounded_and_isolated():
    store=ContinuityStore()
    for i in range(12):
        store.start('s',f'Topic {i}')
        store.record_turn('s',f'Question {i}',f'Reply {i}')
    assert len(store.topics('s'))==8 and len(store.recent_turns('s'))==4
    assert not store.topics('other') and not store.recent_turns('other')


def test_delivery_ack_remembers_selected_topic_after_unrelated_question():
    store=ContinuityStore();agenda,version=saved(store,request='Meeting agenda')
    question=store.start('s','What is a metaphor?')
    store.record_turn('s','What is a metaphor?','A comparison.')
    store.select('s',agenda['id'])
    store.record_turn('s','Email the agenda.','The details were submitted.')
    turns=store.recent_turns('s')
    assert turns[-2]['task_id']==question['id']
    assert turns[-1]['task_id']==agenda['id']
