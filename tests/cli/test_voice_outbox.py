"""Exercise the durable queue without external SMTP or model calls."""
import json
import threading
from unittest.mock import Mock

import pytest

from hermes_cli import voice_outbox as outbox
from hermes_cli.voice_delivery import TaskStore, StaleTask


@pytest.fixture
def queued(monkeypatch, tmp_path):
    from hermes_constants import get_hermes_home
    sandbox = tmp_path / 'outbox-home'
    sandbox.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(sandbox))
    assert get_hermes_home().resolve() == sandbox.resolve()
    monkeypatch.setattr(outbox, 'kick', Mock())
    store = TaskStore()
    task = store.start('session', 'Write a report')
    task = store.publish('session', task, body='Original report.', summary='Summary.')
    return store, task


def rows():
    db = outbox.connect()
    try:
        return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY created')]
    finally:
        db.close()


def test_queue_is_durable_and_deduplicated(queued):
    store, task = queued
    assert outbox.enqueue(store, 'session', task, 'a@example.com') == 'queued'
    assert outbox.enqueue(store, 'session', task, 'a@example.com') == 'queued'
    assert len(rows()) == 1
    sender = Mock(return_value={'success': True})
    outbox.drain(sender)
    outbox.drain(sender)
    sender.assert_called_once_with('a@example.com', 'Original report.')
    assert rows()[0]['status'] == 'accepted'
    assert json.loads(rows()[0]['receipts'])[0]['success'] is True


def test_background_sending_does_not_hold_new_queue_entry(queued):
    store, task = queued
    outbox.enqueue(store, 'session', task, 'a@example.com')
    started, release = threading.Event(), threading.Event()
    def sender(*args):
        started.set()
        assert release.wait(5)
        return {'success': True}
    worker = threading.Thread(target=outbox.drain, args=(sender,))
    worker.start()
    try:
        assert started.wait(5)
        assert outbox.enqueue(store, 'session', task, 'b@example.com') == 'queued'
        assert not release.is_set()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert all(row['status'] == 'accepted' for row in rows())


def test_new_task_does_not_change_authorized_snapshot(queued):
    store, task = queued
    outbox.enqueue(store, 'session', task, 'a@example.com')
    store.start('session', 'An unrelated question')
    sender = Mock(return_value={'success': True})
    outbox.drain(sender)
    sender.assert_called_once_with('a@example.com', 'Original report.')
    with pytest.raises(StaleTask):
        outbox.enqueue(store, 'session', task, 'b@example.com')


def test_crashed_worker_is_not_retried(queued):
    outbox.enqueue(queued[0], 'session', queued[1], 'a@example.com')
    with outbox.connect() as db:
        db.execute("UPDATE jobs SET status='running'")
    sender = Mock()
    outbox.drain(sender)
    sender.assert_not_called()
    assert rows()[0]['status'] == 'unconfirmed'


def test_offline_stops_recipient_group(queued):
    outbox.enqueue(queued[0], 'session', queued[1], 'a@example.com, b@example.com')
    sender = Mock(return_value={'success': False, 'error_code': 'transport_unreachable',
                               'definitive_not_accepted': True})
    outbox.drain(sender)
    outbox.drain(sender)
    assert sender.call_count == 1
    assert rows()[0]['status'] == 'offline'


def test_launch_failure_preserves_job(queued, monkeypatch):
    monkeypatch.setattr(outbox, 'kick', Mock(side_effect=OSError('launch failed')))
    assert outbox.enqueue(queued[0], 'session', queued[1], 'a@example.com') == 'saved'
    assert rows()[0]['status'] == 'queued'


def test_explicit_retry_after_offline_can_recover(queued):
    store, task = queued
    outbox.enqueue(store, 'session', task, 'a@example.com')
    outbox.drain(Mock(return_value={'error_code':'transport_unreachable', 'definitive_not_accepted':True}))
    assert outbox.enqueue(store, 'session', task, 'a@example.com') == 'queued'
    sender = Mock(return_value={'success':True})
    outbox.drain(sender)
    sender.assert_called_once()
    assert rows()[0]['status'] == 'accepted'


def test_fresh_process_uses_registered_sender_and_shared_timeout(queued, monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from hermes_constants import get_hermes_home
    outbox.enqueue(queued[0], 'session', queued[1], 'a@example.com')
    sandbox = tmp_path / 'outbox-home'
    assert get_hermes_home().resolve() == sandbox.resolve()
    (sandbox/'config.yaml').write_text('platforms:\n  email:\n    enabled: true\n', encoding='utf-8')
    env=os.environ.copy()
    env.update(EMAIL_ADDRESS='sender@example.com', EMAIL_PASSWORD='fake-test-token',
               EMAIL_SMTP_HOST='smtp.example.com', EMAIL_SMTP_PORT='587')
    code='''
from unittest.mock import MagicMock
import smtplib
factory=MagicMock()
smtplib.SMTP=factory
smtplib.SMTP_SSL=factory
from hermes_cli.voice_outbox import drain
drain()
assert factory.call_count == 1, factory.call_count
assert factory.call_args.kwargs['timeout'] == 8, factory.call_args
factory.return_value.send_message.assert_called_once()
'''
    result=subprocess.run([sys.executable,'-c',code], cwd=Path(outbox.__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode==0, result.stderr
    assert rows()[0]['status']=='accepted'
