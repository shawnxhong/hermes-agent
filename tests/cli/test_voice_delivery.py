from unittest.mock import Mock
import pytest
from hermes_cli.voice_delivery import TaskStore,StaleTask,delivery_fingerprint


def ready(store,session='s'):
    task=store.start(session,'Draft a project introduction')
    return store.publish(session,task,body='Complete project introduction',summary='A short introduction is ready.')


def test_new_task_identity_clears_facts_question_and_artifact():
    store=TaskStore();old=ready(store)
    old=store.ask_recipient('s',old)
    new=store.start('s','Now plan a workshop')
    assert new['id']!=old['id'] and new['facts']=={} and not new['question_used']
    assert new['artifact_version'] is None and store.artifact('s',old['id']) is None
    with pytest.raises(StaleTask):store.publish('s',old,body='stale',summary='stale')


def test_exactly_one_requirements_question_per_task():
    store=TaskStore();task=store.start('s','Plan a workshop')
    task=store.ask_once('s',task)
    assert task['question_used'] and task['phase']=='awaiting_details'
    task=store.supply_details('s',task,{'audience':'engineers'})
    assert task['phase']=='ready' and store.ask_once('s',task) is None
    new=store.start('s','Draft a project introduction')
    assert store.ask_once('s',new) is not None


def test_artifacts_are_immutable_and_stale_workers_rejected():
    store=TaskStore();first=ready(store)
    second=store.publish('s',first,body='Updated complete report',summary='Updated summary.')
    assert second['artifact_version']==2
    assert store.artifact('s',second['id'])['body']=='Updated complete report'
    with store.connect() as db:
        assert db.execute('SELECT body FROM artifacts WHERE task_id=? AND version=1',(first['id'],)).fetchone()[0]=='Complete project introduction'
    with pytest.raises(StaleTask):store.publish('s',first,body='stale',summary='stale')


def test_pending_typed_address_is_delivery_only_and_same_session():
    store=TaskStore();task=ready(store);task=store.ask_recipient('s',task)
    assert store.recipient_answer('other','other@example.com',platform='cli',modality='text') is None
    assert store.recipient_answer('s','other@example.com',platform='feishu',modality='text') is None
    assert store.recipient_answer('s','Analyze other@example.com',platform='cli',modality='text') is None
    answer=store.recipient_answer('s','other@example.com',platform='cli',modality='text')
    assert answer['recipient']=='other@example.com'
    assert 'recipient' not in store.current('s')
    assert store.recipient_answer('s','other@example.com',platform='cli',modality='text') is None


def test_switch_or_cancel_prevents_old_mailbox_delivery():
    store=TaskStore();task=ready(store);task=store.ask_recipient('s',task)
    store.close('s',task)
    assert store.current('s') is None
    assert store.recipient_answer('s','other@example.com',platform='cli',modality='text') is None


def test_accepted_submission_survives_restart_and_uses_exact_body():
    store=TaskStore();task=ready(store);sender=Mock(return_value={'success':True})
    assert store.submit('s',task,'demo@example.com',sender=sender)=='accepted'
    assert TaskStore().submit('s',task,'demo@example.com',sender=sender)=='accepted'
    sender.assert_called_once_with('demo@example.com','Complete project introduction')


@pytest.mark.parametrize('outcome',[{'success':False},{'error':'timeout'},RuntimeError('SMTP timeout')])
def test_uncertain_submission_never_retries(outcome):
    store=TaskStore();task=ready(store)
    sender=Mock(side_effect=outcome) if isinstance(outcome,Exception) else Mock(return_value=outcome)
    assert store.submit('s',task,'demo@example.com',sender=sender)=='unconfirmed'
    assert store.submit('s',task,'demo@example.com',sender=sender)=='unconfirmed'
    assert sender.call_count==1


def test_pending_submission_after_crash_is_not_retried():
    store=TaskStore();task=ready(store);sender=Mock()
    key=delivery_fingerprint(task['id'],'demo@example.com','Complete project introduction')
    with store.connect() as db:db.execute('INSERT INTO deliveries VALUES (?,?)',(key,'pending'))
    assert store.submit('s',task,'demo@example.com',sender=sender)=='pending'
    sender.assert_not_called()


def test_cancel_and_stale_task_never_call_sender():
    store=TaskStore();task=ready(store);sender=Mock()
    with pytest.raises(StaleTask):store.submit('s',task,'demo@example.com',sender=sender,interrupted=lambda:True)
    store.start('s','Unrelated task')
    with pytest.raises(StaleTask):store.submit('s',task,'demo@example.com',sender=sender)
    sender.assert_not_called()


def test_profile_store_does_not_leak_active_task(tmp_path,monkeypatch):
    store=TaskStore();ready(store)
    monkeypatch.setenv('HERMES_HOME',str(tmp_path/'another-profile'))
    assert store.current('s') is None
