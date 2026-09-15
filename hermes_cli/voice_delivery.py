"""Task-scoped voice artifacts and durable, host-owned email submission.

No model tools, global configuration mutations, or automatic message sends.
The caller must select the task and authorize its recipient before submit_once.
"""
from contextlib import contextmanager
import hashlib
import json
import logging
import re
import sqlite3
import uuid

logger = logging.getLogger(__name__)
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")


def email_recipients(value):
    """Validate a single mailbox or comma-separated default delivery group."""
    if not isinstance(value, str) or '\r' in value or '\n' in value:
        raise ValueError('Valid email recipient(s) required')
    addresses = [part.strip() for part in value.split(',')]
    if not addresses or any(not EMAIL.fullmatch(address) for address in addresses):
        raise ValueError('Valid email recipient(s) required')
    unique = {}
    for address in addresses:
        unique.setdefault(address.casefold(), address)
    return list(unique.values())


def valid_email_recipients(value):
    try:
        return bool(email_recipients(value))
    except ValueError:
        return False


def delivery_fingerprint(scope, recipient, body):
    return hashlib.sha256((scope + "\0" + recipient + "\0" + body).encode()).hexdigest()


def submit_once(fingerprint, *, connect, send, check):
    """Share the existing delivery ledger contract, including legacy travel IDs.

    connect yields a transactional SQLite connection with deliveries(id,status).
    check rejects cancelled/stale work; send returns the native adapter result.
    SMTP acceptance is not a claim of inbox receipt. Pending/uncertain sends
    are never automatically retried, even after a process restart.  A definite
    pre-acceptance physical transport failure uses ``offline`` and may be
    acquired by a later explicit submission; it is safe because SMTP DATA was
    never attempted.
    """
    check()
    with connect() as db:
        inserted = db.execute("INSERT OR IGNORE INTO deliveries VALUES (?,?)", (fingerprint, "pending")).rowcount
        if not inserted:
            status = db.execute("SELECT status FROM deliveries WHERE id=?", (fingerprint,)).fetchone()[0]
            if status != "offline":
                return status
            inserted = db.execute(
                "UPDATE deliveries SET status='pending' WHERE id=? AND status='offline'",
                (fingerprint,),
            ).rowcount
            if not inserted:
                return db.execute("SELECT status FROM deliveries WHERE id=?", (fingerprint,)).fetchone()[0]
    check()
    try:
        result = send()
        if isinstance(result, dict) and result.get("success") is True:
            status = "accepted"
        else:
            from hermes_cli.voice_network import TRANSPORT_UNREACHABLE, classify_network_result

            status = (
                "offline"
                if classify_network_result(result, delivery=True) == TRANSPORT_UNREACHABLE
                and isinstance(result, dict)
                and result.get("definitive_not_accepted") is not False
                else "unconfirmed"
            )
    except Exception:
        logger.exception("Voice email submission failed")
        # An exception escaping the sender carries no reliable SMTP-stage
        # evidence.  It may have happened after DATA was accepted, so fail
        # closed and never retry it automatically.  The standalone adapter
        # returns a structured definitive_not_accepted marker for safe
        # pre-DATA transport retries.
        status = "unconfirmed"
    try:
        with connect() as db:
            db.execute("UPDATE deliveries SET status=? WHERE id=?", (status, fingerprint))
    except Exception:
        logger.exception("Could not persist voice delivery receipt")
        status = "unconfirmed"
    return status


def submit_recipients(scope, recipient, body, *, connect, sender, check):
    """Submit a delivery group with one durable receipt per recipient.

    A physical transport failure stops the loop immediately.  Addresses not
    reached have no ledger row, while previously accepted recipients remain
    deduplicated when the user explicitly retries later.
    """
    addresses = email_recipients(recipient)
    if len(addresses) > 1:
        # Respect a receipt written by the legacy indivisible-group sender.
        # New group_* markers are rollback guards: legacy code treats them as
        # non-retriable, while this implementation may safely continue using
        # the finer per-recipient receipts.
        legacy_key = delivery_fingerprint(scope, ', '.join(addresses), body)
        check()
        with connect() as db:
            row = db.execute("SELECT status FROM deliveries WHERE id=?", (legacy_key,)).fetchone()
        if row and row[0] in {"accepted", "pending", "unconfirmed"}:
            return row[0]
    statuses = []
    for address in addresses:
        status = submit_once(
            delivery_fingerprint(scope, address, body),
            connect=connect,
            send=lambda address=address: sender(address, body),
            check=check,
        )
        statuses.append(status)
        if status == "offline":
            break
    if len(addresses) == 1:
        return statuses[0]
    if len(statuses) == len(addresses) and all(status == "accepted" for status in statuses):
        aggregate = "accepted"
    elif "accepted" in statuses:
        aggregate = "partial"
    elif "offline" in statuses:
        aggregate = "offline"
    else:
        aggregate = "unconfirmed"
    rollback_status = {
        "accepted": "accepted",
        "partial": "group_partial",
        "offline": "group_offline",
        "unconfirmed": "unconfirmed",
    }[aggregate]
    try:
        with connect() as db:
            db.execute(
                "INSERT INTO deliveries VALUES (?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status",
                (legacy_key, rollback_status),
            )
    except Exception:
        logger.exception("Could not persist group delivery rollback guard")
        return "unconfirmed" if aggregate == "accepted" else aggregate
    return aggregate


class StaleTask(RuntimeError):
    """The user switched tasks, or another worker advanced this revision."""


class TaskStore:
    """One active task per session; immutable detail revisions per task."""

    @contextmanager
    def connect(self):
        from hermes_constants import get_hermes_home
        folder = get_hermes_home() / "cache" / "voice-delivery"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(folder / "state.sqlite", timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, task_id TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, revision TEXT, state TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS artifacts (task_id TEXT, version INTEGER, body TEXT, summary TEXT, PRIMARY KEY(task_id,version))")
                db.execute("CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, status TEXT)")
                yield db
        finally:
            db.close()

    def _current(self, db, session):
        row = db.execute("SELECT t.id,t.revision,t.state FROM tasks t JOIN sessions s ON s.task_id=t.id WHERE s.id=?", (session,)).fetchone()
        return dict(id=row['id'], revision=row['revision'], **json.loads(row['state'])) if row else None

    def current(self, session):
        with self.connect() as db:
            return self._current(db, session)

    def start(self, session, request):
        if not session or not isinstance(request, str) or not request.strip():
            raise ValueError('A session and explicit task request are required')
        state = dict(request=request, phase='ready', question_used=False,
                     facts={}, artifact_version=None)
        task_id, revision = uuid.uuid4().hex, uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,?)', (task_id,revision,json.dumps(state)))
            db.execute('INSERT INTO sessions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id', (session,task_id))
        return dict(id=task_id, revision=revision, **state)

    def _require(self, db, session, task_id, revision):
        task = self._current(db, session)
        if not task or task['id']!=task_id or task['revision']!=revision:
            raise StaleTask('Task is no longer current')
        return task

    def _save(self, db, task):
        task = dict(task)
        task_id = task.pop('id');old_revision = task.pop('revision')
        revision = uuid.uuid4().hex
        changed=db.execute('UPDATE tasks SET revision=?,state=? WHERE id=? AND revision=?',
                           (revision,json.dumps(task),task_id,old_revision)).rowcount
        if changed!=1:
            raise StaleTask('Task revision advanced')
        return dict(id=task_id, revision=revision, **task)

    def ask_once(self, session, task, question=None):
        with self.connect() as db:
            current=self._require(db,session,task['id'],task['revision'])
            if current['question_used']:
                return None
            current.update(question_used=True,phase='awaiting_details')
            if isinstance(question,str):
                current['pending_question']=question
                current['last_question']=question
            return self._save(db,current)

    def await_details(self, session, task, question):
        """Record a model-owned essential question without a fixed turn count."""
        if not isinstance(question,str) or not question.strip():
            raise ValueError('A nonempty requirements question is required')
        with self.connect() as db:
            current=self._require(db,session,task['id'],task['revision'])
            current.update(question_used=True,phase='awaiting_details',
                           pending_question=question,last_question=question)
            return self._save(db,current)

    def supply_details(self, session, task, facts):
        if not isinstance(facts,dict):
            raise ValueError('Task facts must be an object')
        with self.connect() as db:
            current=self._require(db,session,task['id'],task['revision'])
            current['facts'].update(facts)
            current['phase']='ready'
            current.pop('pending_question',None)
            return self._save(db,current)

    def publish(self, session, task, *, body, summary):
        if not isinstance(body,str) or not body.strip() or not isinstance(summary,str) or not summary.strip():
            raise ValueError('A complete body and separate summary are required')
        with self.connect() as db:
            current=self._require(db,session,task['id'],task['revision'])
            version=(current['artifact_version'] or 0)+1
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?)',(task['id'],version,body,summary))
            current.update(artifact_version=version,phase='result_ready')
            return self._save(db,current)

    def artifact(self, session, task_id):
        with self.connect() as db:
            task=self._current(db,session)
            if not task or task['id']!=task_id or not task['artifact_version']:
                return None
            return dict(db.execute('SELECT * FROM artifacts WHERE task_id=? AND version=?',
                                   (task_id,task['artifact_version'])).fetchone())

    def ask_recipient(self, session, task):
        with self.connect() as db:
            current=self._require(db,session,task['id'],task['revision'])
            if not current['artifact_version']:
                raise ValueError('Cannot ask for a recipient before saving the result')
            current['phase']='awaiting_recipient'
            return self._save(db,current)

    def recipient_answer(self, session, text, *, platform, modality):
        if platform not in {'cli','local'} or modality not in {'voice','text'}:
            return None
        if not isinstance(text,str) or not EMAIL.fullmatch(text.strip()):
            return None
        with self.connect() as db:
            task=self._current(db,session)
            if not task or task['phase']!='awaiting_recipient':
                return None
            task['phase']='result_ready'
            task=self._save(db,task)
        # Explicit delivery-only value, deliberately absent from persisted task.
        return {'task':task,'recipient':text.strip()}

    def close(self, session, task):
        with self.connect() as db:
            self._require(db,session,task['id'],task['revision'])
            db.execute('UPDATE sessions SET task_id=NULL WHERE id=?',(session,))

    def submit(self, session, task, recipient, *, sender, interrupted=lambda:False):
        recipient = ', '.join(email_recipients(recipient))
        artifact=self.artifact(session,task['id'])
        if not artifact:
            raise StaleTask('No current artifact to deliver')
        def check():
            if interrupted():
                raise StaleTask('Delivery cancelled')
            with self.connect() as db:
                self._require(db,session,task['id'],task['revision'])
        return submit_recipients(task['id'],recipient,artifact['body'],
                                 connect=self.connect,sender=sender,check=check)
