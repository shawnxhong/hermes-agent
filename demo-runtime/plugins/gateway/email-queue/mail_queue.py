"""Private durable queue; one accepted job per native turn, bounded asynchronous delivery retries."""
import argparse
from contextlib import closing
from email.message import EmailMessage
from email.policy import SMTP
from hermes_cli.email_render import render_email
from email.utils import formatdate
import fcntl
import importlib.util
import json
import logging
import os
from pathlib import Path
import signal
import smtplib
import socketserver
import sqlite3
import threading
import time
import uuid


log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS = (10, 30)  # Seconds after the first and second unsuccessful attempts.


class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        self.path = self.folder/'queue.sqlite'
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with closing(self.connect()) as db, db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, scope TEXT UNIQUE NOT NULL, subject TEXT NOT NULL,
              body TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS recipients (
              job TEXT NOT NULL, address TEXT NOT NULL, status TEXT NOT NULL,
              message_id TEXT NOT NULL, error TEXT, PRIMARY KEY(job,address));
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(recipients)')}
            if 'attempts' not in columns:
                db.execute('ALTER TABLE recipients ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
                # Existing in-flight submissions have already consumed an attempt.
                db.execute("UPDATE recipients SET attempts=1 WHERE status='sending'")
            if 'next_attempt' not in columns:
                db.execute('ALTER TABLE recipients ADD COLUMN next_attempt REAL NOT NULL DEFAULT 0')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=0.5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def enqueue(self, request, addresses):
        if not isinstance(request, dict) or set(request) != {'scope','subject','body'}:
            raise ValueError('Invalid request fields')
        scope, subject, body = (request[k] for k in ('scope','subject','body'))
        if (not isinstance(scope, list) or len(scope) != 3
                or not all(isinstance(s,str) and 0 < len(s) <= 512 for s in scope)
                or not isinstance(subject,str) or not 0 < len(subject.strip()) <= 200
                or '\r' in subject or '\n' in subject or not isinstance(body,str)
                or not 0 < len(body.strip()) <= 65536):
            raise ValueError('Invalid scope, subject or body')
        addresses = sorted(set(addresses))
        if not addresses or any(not isinstance(a,str) or '@' not in a or any(c in a for c in '\r\n') for a in addresses):
            raise ValueError('Default recipients unavailable')
        key = json.dumps(scope, separators=(',',':'))
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id,status FROM jobs WHERE scope=?',(key,)).fetchone()
            if old:
                return {'status':'duplicate','job_id':old['id'],'job_status':old['status'],
                        'instruction':'Already accepted for this turn. Stop calling tools and give one final brief queued acknowledgement. Do not rewrite or resubmit.'}
            job = uuid.uuid4().hex
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',(job,key,subject,body,'queued',time.time()))
            for address in addresses:
                db.execute('INSERT INTO recipients (job,address,status,message_id,error) VALUES (?,?,?,?,?)',
                           (job,address,'queued',f'<{uuid.uuid4().hex}@hermes.local>',None))
        return {'status':'queued','job_id':job,'instruction':'Accepted. Stop calling tools and give one final brief queued acknowledgement. SMTP delivery runs in the background; do not rewrite, resubmit, poll or claim delivery.'}

    @staticmethod
    def finish_job(db, job_id):
        states = [r[0] for r in db.execute('SELECT status FROM recipients WHERE job=?', (job_id,))]
        final = ('queued' if any(s in {'queued', 'sending'} for s in states)
                 else 'sent' if all(s == 'sent' for s in states)
                 else 'unknown' if 'unknown' in states
                 else 'partial' if 'sent' in states else 'failed')
        db.execute('UPDATE jobs SET status=? WHERE id=?', (final, job_id))

    def recover(self):
        with closing(self.connect()) as db, db:
            # Only resume jobs interrupted while active. Historical terminal jobs
            # (including failed/unknown/skipped) must never be resurrected.
            jobs = list(db.execute("SELECT id FROM jobs WHERE status='sending'"))
            for job in jobs:
                db.execute("""UPDATE recipients SET
                    status=CASE WHEN attempts < ? THEN 'queued' ELSE 'unknown' END,
                    error='worker_interrupted', next_attempt=?
                    WHERE job=? AND status='sending'""",
                    (MAX_ATTEMPTS, time.time() + RETRY_DELAYS[-1], job['id']))
                self.finish_job(db, job['id'])

    def drain_one(self, send):
        # Reserve one due recipient durably BEFORE making any network request.
        # Delayed retries never sleep in the sender or block newer queue jobs.
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""SELECT r.* FROM recipients r JOIN jobs j ON j.id=r.job
                WHERE j.status='queued' AND r.status='queued'
                  AND r.attempts < ? AND r.next_attempt <= ?
                ORDER BY j.created, r.address LIMIT 1""", (MAX_ATTEMPTS, time.time())).fetchone()
            if row is None:
                return False
            recipient = dict(row)
            job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (recipient['job'],)).fetchone())
            db.execute("UPDATE jobs SET status='sending' WHERE id=?", (job['id'],))
            recipient['attempts'] += 1
            db.execute("UPDATE recipients SET status='sending',attempts=? WHERE job=? AND address=?",
                       (recipient['attempts'], job['id'], recipient['address']))
        started = time.monotonic()
        log.info('mail_attempt_started job=%s attempt=%d queue_age_ms=%.0f',
                 job['id'], recipient['attempts'], (time.time()-job['created'])*1000)
        try:
            state, error, _abort = send(job, recipient)
            if state not in {'sent', 'failed', 'unknown'}:
                raise ValueError('Invalid sender result')
        except Exception:
            state, error = 'unknown', 'unexpected_sender_failure'
        log.info('mail_attempt_finished job=%s attempt=%d status=%s elapsed_ms=%.0f error=%s',
                 job['id'], recipient['attempts'], state, (time.monotonic()-started)*1000, error)
        next_attempt = 0
        if state != 'sent' and recipient['attempts'] < MAX_ATTEMPTS:
            state = 'queued'
            next_attempt = time.time() + RETRY_DELAYS[recipient['attempts'] - 1]
        with closing(self.connect()) as db, db:
            db.execute('UPDATE recipients SET status=?,error=?,next_attempt=? WHERE job=? AND address=?',
                       (state, error, next_attempt, job['id'], recipient['address']))
            self.finish_job(db, job['id'])
        return True

    def status(self):
        with closing(self.connect()) as db:
            return [dict(r) for r in db.execute('SELECT id,status,created FROM jobs ORDER BY created DESC LIMIT 30')]


class SMTPSender:
    def __init__(self, adapter):
        self.adapter = adapter

    def __call__(self, job, recipient):
        adapter = self.adapter
        smtp = None
        phase = 'render'
        started = time.monotonic()
        try:
            msg = EmailMessage(policy=SMTP)
            msg['From'],msg['To'] = adapter._address,recipient['address']
            msg['Subject'],msg['Message-ID'] = job['subject'],recipient['message_id']
            msg['Date'] = formatdate(localtime=True)
            plain, html = render_email(job['body'])
            msg.set_content(plain, charset='utf-8', cte='quoted-printable')
            msg.add_alternative(html, subtype='html', charset='utf-8', cte='quoted-printable')
            phase = 'connect'
            smtp = adapter._connect_smtp()
            smtp.login(adapter._address,adapter._password)
            phase = 'submit'
            refused = smtp.send_message(msg)
            log.info('mail_smtp_result job=%s accepted=%s elapsed_ms=%.0f',
                     job.get('id', 'synthetic'), not bool(refused), (time.monotonic()-started)*1000)
            return ('failed','recipient_refused',False) if refused else ('sent',None,False)
        except smtplib.SMTPRecipientsRefused:
            return 'failed','recipient_refused',False
        except smtplib.SMTPResponseException:
            return 'failed','smtp_rejected',phase=='connect'
        except Exception:
            if phase == 'render':
                return 'failed','message_render_failure',True
            return ('failed','connection_or_auth_failure',True) if phase=='connect' else ('unknown','smtp_outcome_unknown',True)
        finally:
            if smtp:
                try:
                    smtp.quit()
                except Exception:
                    try:
                        smtp.close()
                    except Exception:
                        pass


def production_settings(home):
    from dotenv import load_dotenv
    load_dotenv(home/'.env')
    from gateway.config import Platform, load_gateway_config
    from hermes_constants import get_hermes_home
    import hermes_constants
    assert Path(get_hermes_home()).resolve() == home.resolve()
    root = Path(hermes_constants.__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('_mail_queue_adapter', root/'plugins/platforms/email/adapter.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = load_gateway_config()
    adapter = module.EmailAdapter(config.platforms[Platform.EMAIL])
    def recipients():
        target = load_gateway_config().get_home_channel(Platform.EMAIL)
        return [a.strip() for a in target.chat_id.split(',') if a.strip()] if target else []
    return SMTPSender(adapter), recipients


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path, store, addresses):
        self.store,self.addresses = store,addresses
        super().__init__(str(path), Handler)
        os.chmod(path,0o600)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(1)
        try:
            raw = self.rfile.readline(1048576)
            if not raw.endswith(b'\n') or len(raw) >= 1048576:
                raise ValueError('Invalid request size')
            result = self.server.store.enqueue(json.loads(raw),self.server.addresses())
        except (ValueError, TypeError, OSError, sqlite3.Error):
            result = {'status':'rejected','error':'Local queue rejected request. Do not retry automatically.'}
        try:
            self.wfile.write((json.dumps(result)+'\n').encode())
        except OSError:
            pass  # Already committed jobs survive a lost acknowledgement.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['serve','status'])
    parser.add_argument('--home',type=Path,required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    os.umask(0o077)
    os.environ['HERMES_HOME'] = str(args.home)
    if args.action == 'status':
        path = args.home/'mail-queue/queue.sqlite'
        with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as db:
            db.row_factory = sqlite3.Row
            jobs=[dict(r) for r in db.execute('SELECT id,status,created FROM jobs ORDER BY created DESC LIMIT 30')]
            for job in jobs:
                job['recipients']=[dict(r) for r in db.execute(
                    'SELECT address,status,error,attempts,next_attempt FROM recipients WHERE job=?',(job['id'],))]
            print(json.dumps(jobs,indent=2))
        return
    store = Store(args.home/'mail-queue')
    lock = open(store.folder/'service.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    sender,addresses = production_settings(args.home)
    store.recover()
    path = store.folder/'service.sock'
    if path.exists():
        path.unlink()
    stop = threading.Event()
    def worker():
        while not stop.is_set():
            try:
                if not store.drain_one(sender):
                    stop.wait(0.25)
            except Exception:
                # Restart/recover conservatively rather than leave a live but
                # non-delivering service. Never log bodies or SMTP secrets.
                os._exit(1)
    with Server(path,store,addresses) as server:
        threading.Thread(target=worker,daemon=True).start()
        def shutdown(*unused):
            stop.set()
            threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,shutdown)
        signal.signal(signal.SIGINT,shutdown)
        server.serve_forever(poll_interval=0.2)
    path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
