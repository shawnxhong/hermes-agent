"""Durable local voice email queue; delivery never blocks spoken replies.

Jobs snapshot the authorized recipient list and artifact. A separate process
survives CLI exit. Only queued jobs are resumed; interrupted sends remain
unconfirmed because SMTP acceptance may have occurred before a crash.
"""
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from hermes_constants import get_hermes_home
from hermes_cli.voice_delivery import TaskStore, delivery_fingerprint, email_recipients, submit_recipients

log = logging.getLogger(__name__)


def connect():
    folder = get_hermes_home() / 'cache' / 'voice-delivery'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / 'outbox.sqlite'
    # Create privately before SQLite opens it (contains full result text).
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS jobs '
               '(id TEXT PRIMARY KEY, scope TEXT, recipient TEXT, body TEXT, '
               'status TEXT, created REAL, finished REAL, elapsed REAL, receipts TEXT)')
    db.commit()
    return db


def kick():
    """Spawn without waiting; each worker locks the queue before draining."""
    env = os.environ.copy()
    env['HERMES_HOME'] = str(get_hermes_home())
    process = subprocess.Popen(
        [sys.executable, '-m', 'hermes_cli.voice_outbox'],
        cwd=str(Path(__file__).resolve().parents[1]), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    # Reap the launcher child without holding up the CLI.
    import threading
    threading.Thread(target=process.wait, daemon=True).start()


def enqueue(store, session, task, recipient, interrupted=lambda: False, *, version=None):
    if interrupted():
        raise RuntimeError('Delivery cancelled before queueing')
    with store.connect() as current_db:
        store._require(current_db, session, task['id'], task['revision'])
    artifact = (store.result(session, task['id'], version) if version is not None
                else store.artifact(session, task['id']))
    if not artifact:
        raise RuntimeError('No current artifact to queue')
    addresses = ', '.join(email_recipients(recipient))
    if not addresses:
        raise ValueError('No email recipient')
    key = delivery_fingerprint(task['id'], addresses, artifact['body'])
    db = connect()
    try:
        with db:
            db.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                       (key, task['id'], addresses, artifact['body'], 'queued',
                        time.time(), None, None, None))
            # Only a new explicit submission may retry a definite failure.
            # The per-recipient ledger still protects accepted/uncertain sends.
            db.execute("UPDATE jobs SET status='queued' WHERE id=? AND status IN ('offline','partial')", (key,))
        status = db.execute('SELECT status FROM jobs WHERE id=?', (key,)).fetchone()[0]
    finally:
        db.close()
    if status == 'queued':
        try:
            kick()
        except Exception:
            log.exception('Voice email queue saved; background worker could not start')
            return 'saved'
    log.info('voice_latency stage=email_enqueue job=%s status=%s', key, status)
    return 'queued' if status in {'queued', 'running'} else status


def resume():
    """Resume untouched queued jobs on a later voice turn, never retry sends."""
    path = get_hermes_home() / 'cache' / 'voice-delivery' / 'outbox.sqlite'
    if not path.exists():
        return
    db = connect()
    try:
        pending = db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()
    finally:
        db.close()
    if pending:
        kick()


def drain(sender=None):
    import fcntl
    folder = get_hermes_home() / 'cache' / 'voice-delivery'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / 'outbox.lock').open('a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        db = connect()
        try:
            # The exclusive process lock proves any prior running job lost
            # its worker. Do not resend an ambiguous in-flight SMTP message.
            with db:
                db.execute("UPDATE jobs SET status='unconfirmed', finished=? WHERE status='running'",
                           (time.time(),))
            if sender is None:
                from hermes_cli.env_loader import load_hermes_dotenv
                load_hermes_dotenv()
                from hermes_cli.plugins import discover_plugins
                discover_plugins()
                from hermes_cli.general_voice import _send
                sender = _send
            while True:
                row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if row is None:
                    return
                with db:
                    db.execute("UPDATE jobs SET status='running' WHERE id=?", (row['id'],))
                started = time.monotonic()
                receipts = []

                def timed_send(address, body):
                    before = time.monotonic()
                    try:
                        result = sender(address, body)
                    except Exception as exc:
                        result = {'success': False, 'error_code': type(exc).__name__}
                    receipts.append({'recipient': address, 'seconds': round(time.monotonic()-before, 3),
                                     'success': isinstance(result, dict) and result.get('success') is True,
                                     'error_code': result.get('error_code') if isinstance(result, dict) else None,
                                     'stage': result.get('delivery_stage') if isinstance(result, dict) else None})
                    return result

                try:
                    status = submit_recipients(row['scope'], row['recipient'], row['body'],
                                               connect=TaskStore().connect, sender=timed_send, check=lambda: None)
                except Exception:
                    log.exception('Background voice email failed')
                    status = 'unconfirmed'
                elapsed = time.monotonic() - started
                with db:
                    db.execute('UPDATE jobs SET status=?,finished=?,elapsed=?,receipts=? WHERE id=?',
                               (status, time.time(), elapsed, json.dumps(receipts), row['id']))
                log.info('voice_latency stage=email_background job=%s status=%s seconds=%.3f',
                         row['id'], status, elapsed)
        finally:
            db.close()


if __name__ == '__main__':
    drain()
