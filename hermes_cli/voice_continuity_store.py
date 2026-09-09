"""Session-owned content and one-shot interactions, independent of delivery.

Additive tables share the existing voice-delivery database/SMTP ledger. Legacy
tasks are imported only through a proven current session link, never guessed.
"""
from contextlib import contextmanager
import json
import uuid

from hermes_cli.voice_delivery import TaskStore, StaleTask, EMAIL, delivery_fingerprint, submit_once


class ContinuityStore(TaskStore):
    @contextmanager
    def connect(self):
        with super().connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS topic_links (session_id TEXT, task_id TEXT UNIQUE, touched INTEGER, PRIMARY KEY(session_id,task_id))')
            db.execute('CREATE TABLE IF NOT EXISTS result_metadata (task_id TEXT, version INTEGER, kind TEXT, parent_version INTEGER, sources TEXT, PRIMARY KEY(task_id,version))')
            if 'detailed' not in {row[1] for row in db.execute('PRAGMA table_info(result_metadata)')}:
                db.execute('ALTER TABLE result_metadata ADD COLUMN detailed INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE TABLE IF NOT EXISTS voice_interactions (id TEXT PRIMARY KEY, session_id TEXT, task_id TEXT, revision TEXT, kind TEXT, payload TEXT, status TEXT, repeats INTEGER)')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_voice_pending ON voice_interactions(session_id) WHERE status="pending"')
            db.execute('CREATE TABLE IF NOT EXISTS voice_turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, request TEXT, reply TEXT)')
            # These are the only legacy ownership relationships we can prove.
            db.execute('INSERT OR IGNORE INTO topic_links SELECT id,task_id,0 FROM sessions WHERE task_id IS NOT NULL')
            yield db

    def _owned(self, db, session, task_id):
        if not db.execute('SELECT 1 FROM topic_links WHERE session_id=? AND task_id=?',(session,task_id)).fetchone():
            raise StaleTask('Result is not owned by this session')

    def _invalidate(self, db, session):
        db.execute('UPDATE voice_interactions SET status="cancelled" WHERE session_id=? AND status="pending"',(session,))

    def start(self, session, request, *, language='en'):
        if not session or not isinstance(request,str) or not request.strip():
            raise ValueError('A session and explicit task request are required')
        state=dict(request=request,language='zh' if language=='zh' else 'en',phase='ready',question_used=False,facts={},artifact_version=None)
        task_id,revision=uuid.uuid4().hex,uuid.uuid4().hex
        with self.connect() as db:
            self._invalidate(db,session)
            db.execute('INSERT INTO tasks VALUES (?,?,?)',(task_id,revision,json.dumps(state)))
            db.execute('INSERT INTO sessions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id',(session,task_id))
            db.execute('INSERT INTO topic_links VALUES (?,?,(SELECT COALESCE(MAX(touched),0)+1 FROM topic_links))',(session,task_id))
        return dict(id=task_id,revision=revision,**state)

    def select(self, session, task_id):
        with self.connect() as db:
            self._owned(db,session,task_id)
            current=self._current(db,session)
            if current and current['id']==task_id:
                return current
            self._invalidate(db,session)
            # Rotate revision even on return to the same topic: reject ABA workers.
            db.execute('UPDATE tasks SET revision=? WHERE id=?',(uuid.uuid4().hex,task_id))
            db.execute('INSERT INTO sessions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id',(session,task_id))
            db.execute('UPDATE topic_links SET touched=(SELECT MAX(touched)+1 FROM topic_links) WHERE task_id=?',(task_id,))
            return self._current(db,session)

    def topics(self, session, *, query='', limit=8):
        limit=min(8,max(1,int(limit)))
        with self.connect() as db:
            rows=db.execute('SELECT t.id,t.state FROM tasks t JOIN topic_links l ON l.task_id=t.id WHERE l.session_id=? ORDER BY l.touched DESC',(session,)).fetchall()
            values=[]
            for row in rows:
                state=json.loads(row['state'])
                result=db.execute('SELECT a.version,a.summary,COALESCE(m.detailed,0) AS detailed FROM artifacts a LEFT JOIN result_metadata m ON m.task_id=a.task_id AND m.version=a.version WHERE a.task_id=? ORDER BY a.version DESC LIMIT 1',(row['id'],)).fetchone()
                item={'id':row['id'],'request':state['request'][:500], 'phase':state['phase'],
                      'version':result['version'] if result else None,'detailed':bool(result['detailed']) if result else False,'summary':result['summary'][:600] if result else ''}
                values.append(item)
            if query:
                terms=set(query.casefold().split())
                values.sort(key=lambda item:sum(term in (item['request']+' '+item['summary']).casefold() for term in terms),reverse=True)
            return values[:limit]

    def record_turn(self, session, request, reply):
        with self.connect() as db:
            db.execute('INSERT INTO voice_turns(session_id,request,reply) VALUES (?,?,?)',(session,request[:2000],reply[:2000]))

    def recent_turns(self, session):
        with self.connect() as db:
            rows=db.execute('SELECT request,reply FROM voice_turns WHERE session_id=? ORDER BY id DESC LIMIT 4',(session,)).fetchall()
            return [dict(row) for row in reversed(rows)]

    def save_result(self, session, task, *, body, summary, kind='main', parent_version=None, sources=(), detailed=False):
        if kind not in {'main','revision','explanation'} or not body.strip() or not summary.strip():
            raise ValueError('A complete result and valid kind are required')
        with self.connect() as db:
            current=self._require(db,session,task['id'],task['revision'])
            self._owned(db,session,task['id'])
            if parent_version is not None and not db.execute('SELECT 1 FROM artifacts WHERE task_id=? AND version=?',(task['id'],parent_version)).fetchone():
                raise StaleTask('Parent result is missing')
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM artifacts WHERE task_id=?',(task['id'],)).fetchone()[0]
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?)',(task['id'],version,body,summary))
            db.execute('INSERT INTO result_metadata VALUES (?,?,?,?,?,?)',(task['id'],version,kind,parent_version,json.dumps(list(sources)),int(detailed)))
            if kind!='explanation':
                current.update(artifact_version=version,phase='result_ready')
            current['latest_result_version']=version
            current=self._save(db,current)
            self._invalidate(db,session)
            return current,version

    def result(self, session, task_id, version=None):
        with self.connect() as db:
            self._owned(db,session,task_id)
            if version is None:
                row=db.execute('SELECT * FROM artifacts WHERE task_id=? ORDER BY version DESC LIMIT 1',(task_id,)).fetchone()
            else:
                row=db.execute('SELECT * FROM artifacts WHERE task_id=? AND version=?',(task_id,version)).fetchone()
            if not row:return None
            result=dict(row)
            metadata=db.execute('SELECT detailed FROM result_metadata WHERE task_id=? AND version=?',(task_id,result['version'])).fetchone()
            result['detailed']=bool(metadata['detailed']) if metadata else False
            return result

    def pend(self, session, task, kind, payload):
        if kind not in {'requirements','reference','recipient','confirm_recipient'}:
            raise ValueError('Not a voice-content interaction')
        with self.connect() as db:
            self._require(db,session,task['id'],task['revision'])
            self._invalidate(db,session)
            identity=uuid.uuid4().hex
            db.execute('INSERT INTO voice_interactions VALUES (?,?,?,?,?,?,"pending",0)',
                       (identity,session,task['id'],task['revision'],kind,json.dumps(payload)))
        return identity

    def pending(self, session):
        with self.connect() as db:
            row=db.execute('SELECT * FROM voice_interactions WHERE session_id=? AND status="pending"',(session,)).fetchone()
            if not row:return None
            current=self._current(db,session)
            if not current or current['id']!=row['task_id'] or current['revision']!=row['revision']:
                self._invalidate(db,session)
                return None
            value=dict(row);value['payload']=json.loads(value['payload']);return value

    def consume(self, session, identity, *, status='consumed'):
        if status not in {'consumed','cancelled','paused'}:raise ValueError('Invalid interaction status')
        with self.connect() as db:
            row=db.execute('SELECT * FROM voice_interactions WHERE id=? AND session_id=? AND status="pending"',(identity,session)).fetchone()
            if not row:raise StaleTask('Confirmation is no longer pending')
            self._require(db,session,row['task_id'],row['revision'])
            changed=db.execute('UPDATE voice_interactions SET status=? WHERE id=? AND status="pending"',(status,identity)).rowcount
            if changed!=1:raise StaleTask('Confirmation already consumed')
            return json.loads(row['payload'])

    def unclear(self, session, identity):
        with self.connect() as db:
            row=db.execute('SELECT * FROM voice_interactions WHERE id=? AND session_id=? AND status="pending"',(identity,session)).fetchone()
            if not row:raise StaleTask('No pending question')
            self._require(db,session,row['task_id'],row['revision'])
            repeats=row['repeats']+1
            db.execute('UPDATE voice_interactions SET repeats=?,status=? WHERE id=?',
                       (repeats,'paused' if repeats>1 else 'pending',identity))
            return repeats>1

    def close(self, session, task):
        with self.connect() as db:
            self._require(db,session,task['id'],task['revision'])
            self._invalidate(db,session)
            db.execute('UPDATE sessions SET task_id=NULL WHERE id=?',(session,))

    def submit_result(self, session, task, version, recipient, *, sender, interrupted=lambda:False):
        if not isinstance(recipient,str) or not EMAIL.fullmatch(recipient):raise ValueError('Valid recipient required')
        result=self.result(session,task['id'],version)
        if not result:raise StaleTask('No selected result')
        def check():
            if interrupted():raise StaleTask('Delivery cancelled')
            with self.connect() as db:self._require(db,session,task['id'],task['revision'])
        return submit_once(delivery_fingerprint(task['id'],recipient,result['body']),connect=self.connect,
                           send=lambda:sender(recipient,result['body']),check=check)
