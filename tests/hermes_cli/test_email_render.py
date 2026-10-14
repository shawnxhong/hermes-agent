"""Presentation and real local queue -> SMTP contracts; no external recipients."""
import importlib.util
import json
from pathlib import Path
import smtplib
import socket
import socketserver
import threading
from email import policy
from email.parser import BytesParser

import pytest

from hermes_cli.email_render import render_email


BODY = '''# 中文行程 café 😀
第一行 & 第二行
下一行

## 事项
- **护照**
- [来源](https://example.invalid/a?x=1&y=2)

| 日期 | 内容 |
| --- | --- |
| 周一 | 出发 |

> 引用说明

```python
path = r"C:\\new\\test"
print("<测试>")

# keep blank line
```
'''


def test_markdown_structure_and_unicode():
    plain, html = render_email(BODY)
    for text in ('中文行程 café 😀', '第一行 & 第二行', '下一行', '护照', '周一', '出发'):
        assert text in plain
    for tag in ('<h1', '<h2', '<strong>', '<ul', '<table', '<blockquote', '<pre'):
        assert tag in html
    assert '第一行 &amp; 第二行<br' in html
    assert '下一行' in html
    assert '- 护照' in plain
    assert '日期\t内容' in plain
    assert '<https://example.invalid/a?x=1&y=2>' in plain
    assert 'print("<测试>")\n\n# keep blank line' in plain
    assert r'C:\new\test' in plain
    assert '**' not in plain


@pytest.mark.parametrize('body', [
    '<script>alert(1)</script><img src="https://example.invalid/pixel" onerror="oops">',
    '[click](javascript:alert%281%29)',
    '[click](data:text/html,payload)',
    '[click](vbscript:msgbox)',
    '[click](//example.invalid/path)',
    '[click](java&#x73;cript:alert)',
    '[click](https://example.invalid/&#10;bad)',
])
def test_untrusted_content_has_no_active_markup(body):
    plain, html = render_email(body)
    assert '<script' not in html
    assert '<img' not in html
    assert ' href=' not in html
    assert plain.strip()


def test_raw_html_is_visible_text_and_images_are_alt_only():
    plain, html = render_email('<b>literal</b>\n\n![地图](https://example.invalid/map)')
    assert '<b>literal</b>' in plain
    assert '&lt;b&gt;literal&lt;/b&gt;' in html
    assert '地图' in plain
    assert '<img' not in html


def test_newlines_literal_escapes_and_ordered_lists():
    plain, html = render_email('a\r\nb\r\n\r\nc\r\n\r\n1. one\r\n2. two\r\n\r\n`literal \\n and C:\\new`')
    assert 'a\nb\n\nc' in plain
    assert '1. one\n2. two' in plain
    assert r'literal \n and C:\new' in plain
    assert '<br' in html


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'220 local test\r\n')
        while True:
            line = self.rfile.readline()
            command = line.split(b' ', 1)[0].strip().upper()
            if command == b'EHLO':
                self.wfile.write(b'250-localhost\r\n250 AUTH PLAIN\r\n')
            elif command == b'AUTH':
                self.wfile.write(b'235 OK\r\n')
            elif command in {b'MAIL', b'RCPT', b'RSET'}:
                self.wfile.write(b'250 OK\r\n')
            elif command == b'DATA':
                self.wfile.write(b'354 continue\r\n')
                data = []
                while True:
                    chunk = self.rfile.readline()
                    if chunk == b'.\r\n':
                        break
                    if not chunk:
                        raise RuntimeError('Incomplete SMTP DATA')
                    data.append(chunk[1:] if chunk.startswith(b'..') else chunk)
                if getattr(self.server, 'reject_remaining', 0):
                    self.server.reject_remaining -= 1
                    self.wfile.write(b'451 temporary delivery failure\r\n')
                    continue
                self.server.messages.append(b''.join(data))
                self.wfile.write(b'250 accepted\r\n')
            elif command == b'QUIT':
                self.wfile.write(b'221 bye\r\n')
                break
            elif not line:
                break
            else:
                self.wfile.write(b'500 unsupported\r\n')


@pytest.mark.parametrize('role', ['cli', 'gateway', 'services'])
def test_real_queue_socket_smtp_and_mime_roundtrip(tmp_path, role):
    path = Path(__file__).resolve().parents[2] / f'demo-runtime/plugins/{role}/email-queue/mail_queue.py'
    spec = importlib.util.spec_from_file_location('queue_' + role, path)
    queue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(queue)
    store = queue.Store(tmp_path / 'queue')
    request = {'scope': ['test-profile', 'test-session', 'test-turn'],
               'subject': '中文主题 café 😀', 'body': BODY}
    sock = tmp_path / 'queue.sock'
    with queue.Server(sock, store, lambda: ['reader@example.invalid']) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def submit():
                with socket.socket(socket.AF_UNIX) as client:
                    client.connect(str(sock))
                    client.sendall((json.dumps(request) + '\n').encode())
                    return json.loads(client.makefile('rb').readline())
            assert submit()['status'] == 'queued'
            assert submit()['status'] == 'duplicate'
        finally:
            server.shutdown()
            thread.join(timeout=5)
    with socketserver.TCPServer(('127.0.0.1', 0), SMTPHandler) as smtp_server:
        smtp_server.messages = []
        thread = threading.Thread(target=smtp_server.serve_forever, daemon=True)
        thread.start()
        class Adapter:
            _address = 'sender@example.invalid'
            _password = 'synthetic-only'
            def _connect_smtp(self):
                return smtplib.SMTP(*smtp_server.server_address, timeout=5)
        try:
            assert store.drain_one(queue.SMTPSender(Adapter()))
            assert not store.drain_one(queue.SMTPSender(Adapter()))
            assert store.status()[0]['status'] == 'sent'
        finally:
            smtp_server.shutdown()
            thread.join(timeout=5)
    assert len(smtp_server.messages) == 1
    wire = smtp_server.messages[0]
    assert all(len(line) <= 998 for line in wire.split(b'\r\n'))
    msg = BytesParser(policy=policy.default).parsebytes(wire)
    assert msg['Subject'] == request['subject']
    assert msg.get_content_type() == 'multipart/alternative'
    assert not msg.defects
    parts = list(msg.iter_parts())
    assert [p.get_content_type() for p in parts] == ['text/plain', 'text/html']
    for part in parts:
        assert part.get_content_charset() == 'utf-8'
        assert part['Content-Transfer-Encoding'] == 'quoted-printable'
        assert '中文行程 café 😀' in part.get_content()
        assert '\ufffd' not in part.get_content()
    assert '第一行 & 第二行\r\n下一行' in parts[0].get_content()
    assert '<table' in parts[1].get_content()


@pytest.mark.parametrize('role', ['cli', 'gateway', 'services'])
def test_transport_outcome_remains_conservative(tmp_path, role):
    path = Path(__file__).resolve().parents[2] / f'demo-runtime/plugins/{role}/email-queue/mail_queue.py'
    spec = importlib.util.spec_from_file_location('queue_errors_' + role, path)
    queue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(queue)
    class Adapter:
        _address = 'sender@example.invalid'
        _password = 'synthetic'
        def _connect_smtp(self):
            raise OSError('synthetic offline')
    job = {'subject': 'test', 'body': BODY}
    recipient = {'address': 'reader@example.invalid', 'message_id': '<test@example.invalid>'}
    assert queue.SMTPSender(Adapter())(job, recipient) == ('failed', 'connection_or_auth_failure', True)


def test_autolink_entities_and_nested_lists():
    plain, html = render_email('A &amp; B <https://example.invalid/x>\n\n1. first\n    - nested\n2. second')
    assert 'A & B' in plain
    assert '&amp;amp;' not in html
    assert 'href="https://example.invalid/x"' in html
    assert '1. first' in plain
    assert '  - nested' in plain
    assert '2. second' in plain


def test_long_unicode_body_wraps_mime_without_data_loss():
    from email.message import EmailMessage
    from email.policy import SMTP
    body = '中文😀&' * 10000
    plain, html = render_email(body)
    msg = EmailMessage(policy=SMTP)
    msg.set_content(plain, charset='utf-8', cte='quoted-printable')
    msg.add_alternative(html, subtype='html', charset='utf-8', cte='quoted-printable')
    wire = msg.as_bytes()
    assert max(map(len, wire.split(b'\r\n'))) <= 998
    parsed = BytesParser(policy=policy.default).parsebytes(wire)
    assert parsed.get_body(preferencelist=('plain',)).get_content().rstrip('\r\n') == body


@pytest.fixture(params=['cli', 'gateway', 'services'])
def retry_queue(tmp_path, request, monkeypatch):
    path = Path(__file__).resolve().parents[2] / f'demo-runtime/plugins/{request.param}/email-queue/mail_queue.py'
    spec = importlib.util.spec_from_file_location('queue_retry_' + request.param, path)
    queue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(queue)
    clock = [1000.0]
    monkeypatch.setattr(queue.time, 'time', lambda: clock[0])
    return queue, queue.Store(tmp_path / 'queue'), clock


def queued(store, turn='t', addresses=None):
    request = {'scope': ['p', 's', turn], 'subject': 'test', 'body': 'one\ntwo'}
    return store.enqueue(request, addresses or ['a@example.invalid'])['job_id']


def recipient_rows(store, job):
    with store.connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM recipients WHERE job=? ORDER BY address', (job,))]


@pytest.mark.parametrize('outcome', ['failed', 'unknown'])
def test_retry_stops_at_three_and_survives_reopen(retry_queue, outcome):
    queue, store, clock = retry_queue
    job = queued(store)
    ids = []
    def send(job, recipient):
        ids.append(recipient['message_id'])
        return outcome, 'synthetic_failure', True
    for attempt, delay in enumerate([10, 30, 0], 1):
        assert store.drain_one(send)
        row = recipient_rows(store, job)[0]
        assert row['attempts'] == attempt
        assert row['status'] == ('queued' if attempt < 3 else outcome)
        assert not store.drain_one(lambda *args: pytest.fail('too early or exhausted'))
        store = queue.Store(store.folder)
        store.recover()
        clock[0] += delay
    assert len(ids) == 3 and len(set(ids)) == 1
    assert store.status()[0]['status'] == outcome
    assert queued(store) == job  # Native turn deduplication remains intact.


def test_retry_does_not_repeat_success_or_block_new_jobs(retry_queue):
    _, store, clock = retry_queue
    job = queued(store, addresses=['a@example.invalid', 'b@example.invalid'])
    calls = []
    def send(job, recipient):
        calls.append(recipient['address'])
        return ('failed', 'offline', True) if len(calls) == 1 else ('sent', None, False)
    assert store.drain_one(send)
    assert store.drain_one(send)  # The other recipient proceeds immediately.
    queued(store, turn='new')
    assert store.drain_one(send)  # A newer job proceeds while the first waits.
    clock[0] += 10
    assert store.drain_one(send)
    assert not store.drain_one(send)
    assert calls == ['a@example.invalid', 'b@example.invalid', 'a@example.invalid', 'a@example.invalid']
    assert [r['attempts'] for r in recipient_rows(store, job)] == [2, 1]
    assert all(j['status'] == 'sent' for j in store.status())


def test_worker_crashes_consume_attempts_and_stop_at_limit(retry_queue):
    queue, store, clock = retry_queue
    job = queued(store)
    def crash(*args):
        raise KeyboardInterrupt()
    for attempt in range(1, 4):
        with pytest.raises(KeyboardInterrupt):
            store.drain_one(crash)
        store = queue.Store(store.folder)
        store.recover()
        assert recipient_rows(store, job)[0]['attempts'] == attempt
        assert store.status()[0]['status'] == ('queued' if attempt < 3 else 'unknown')
        assert not store.drain_one(lambda *args: pytest.fail('early retry'))
        clock[0] += 30
    assert not store.drain_one(crash)


def test_real_smtp_rejection_then_retry_success(retry_queue):
    queue, store, clock = retry_queue
    job = queued(store)
    with socketserver.TCPServer(('127.0.0.1', 0), SMTPHandler) as server:
        server.messages = []
        server.reject_remaining = 1
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        class Adapter:
            _address = 'sender@example.invalid'
            _password = 'synthetic-only'
            def _connect_smtp(self):
                return smtplib.SMTP(*server.server_address, timeout=5)
        try:
            sender = queue.SMTPSender(Adapter())
            assert store.drain_one(sender)
            row = recipient_rows(store, job)[0]
            assert row['status'] == 'queued' and row['error'] == 'smtp_rejected'
            clock[0] += 10
            assert store.drain_one(sender)
            assert store.status()[0]['status'] == 'sent'
            assert recipient_rows(store, job)[0]['attempts'] == 2
            assert not store.drain_one(sender)
            assert len(server.messages) == 1
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_enqueue_ack_does_not_wait_for_smtp(retry_queue):
    queue, store, _ = retry_queue
    entered = threading.Event()
    release = threading.Event()
    stop = threading.Event()
    def slow_sender(*args):
        entered.set()
        assert release.wait(5), 'test must release simulated SMTP'
        return 'sent', None, False
    def worker():
        while not stop.is_set():
            if not store.drain_one(slow_sender):
                stop.wait(0.01)
    from contextlib import ExitStack
    from tempfile import TemporaryDirectory
    with ExitStack() as stack:
        sock = Path(stack.enter_context(TemporaryDirectory(prefix='email-ack-'))) / 's'
        server = stack.enter_context(queue.Server(sock, store, lambda: ['reader@example.invalid']))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        worker_thread = threading.Thread(target=worker, daemon=True)
        server_thread.start(); worker_thread.start()
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(str(sock))
                request = {'scope':['p','s','async'], 'subject':'test', 'body':'# Plan\n- Train'}
                client.sendall((json.dumps(request)+'\n').encode())
                response = json.loads(client.makefile('rb').readline())
            assert response['status'] == 'queued'
            assert entered.wait(2)
            assert not release.is_set()  # Ack was received with SMTP still blocked.
        finally:
            release.set(); stop.set()
            worker_thread.join(timeout=5)
            server.shutdown(); server_thread.join(timeout=5)
        assert not worker_thread.is_alive()
    assert store.status()[0]['status'] == 'sent'


def test_legacy_schema_upgrade_preserves_terminal_jobs(retry_queue, tmp_path):
    import sqlite3
    queue, _, clock = retry_queue
    folder = tmp_path / 'legacy'
    folder.mkdir()
    with sqlite3.connect(folder / 'queue.sqlite') as db:
        db.executescript("""
            CREATE TABLE jobs (id TEXT PRIMARY KEY, scope TEXT UNIQUE NOT NULL,
                subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE recipients (job TEXT NOT NULL, address TEXT NOT NULL, status TEXT NOT NULL,
                message_id TEXT NOT NULL, error TEXT, PRIMARY KEY(job,address));
        """)
        for i, state in enumerate(['sent', 'failed', 'unknown', 'sending']):
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', (str(i), str(i), 'test', 'body', state, clock[0]))
            db.execute('INSERT INTO recipients VALUES (?,?,?,?,?)', (str(i), 'a@example.invalid', state, '<legacy>', None))
    store = queue.Store(folder)
    store.recover()
    assert recipient_rows(store, '3')[0]['attempts'] == 1
    assert recipient_rows(store, '3')[0]['status'] == 'queued'
    clock[0] += 30
    assert store.drain_one(lambda *args: ('sent', None, False))
    assert not store.drain_one(lambda *args: pytest.fail('terminal history replayed'))
    with store.connect() as db:
        assert [r[0] for r in db.execute('SELECT status FROM jobs ORDER BY id')] == ['sent', 'failed', 'unknown', 'sent']


@pytest.mark.parametrize('value', [None, {}, [], [1], [''], [' ', ''], ['x'] * 4097, ['x' * 65536, 'y']])
def test_line_array_rejects_invalid_content(value):
    from hermes_cli.email_render import normalize_email_body
    with pytest.raises(ValueError):
        normalize_email_body(value)


def test_multiline_blocks_do_not_require_format_repair():
    from hermes_cli.email_render import normalize_email_body
    body = '# Travel plan\n\n## Transport\n- Train\n- Flight\n\n## Itinerary\n| Day | Plan |\n| --- | --- |\n| 1 | Museum |'
    assert normalize_email_body([body]) == body
    assert normalize_email_body(['first\r\nsecond', '', 'last']) == 'first\r\nsecond\n\nlast'
    plain, html = render_email(normalize_email_body([body]))
    assert '<h2' in html and '<ul' in html and '<table' in html
    assert 'Train' in plain


def test_line_array_preserves_structure_and_legacy_strings():
    from hermes_cli.email_render import normalize_email_body
    lines = ['# Synthetic trip', '', '## Transport', '1. **Train**', '   - Duration: sample', '',
             '## Notes', '- 中文 café 😀', '', '```python', r'path = "C:\new"', '', '```']
    body = normalize_email_body(lines)
    assert body == '\n'.join(lines)
    assert normalize_email_body(body) == body
    plain, html = render_email(body)
    assert '<h1' in html and '<h2' in html and '<ol' in html and '<ul' in html
    assert '<pre' in html and '中文 café 😀' in plain


@pytest.mark.parametrize('role', ['cli', 'gateway', 'services'])
def test_lines_wire(tmp_path, monkeypatch, role, request):
    import tempfile
    short_home = tempfile.TemporaryDirectory(prefix="email-", dir="/tmp")
    request.addfinalizer(short_home.cleanup)
    tmp_path = Path(short_home.name)
    from types import SimpleNamespace
    from agent import relay_runtime
    from hermes_cli.email_render import normalize_email_body
    root = Path(__file__).resolve().parents[2]
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    plugin = load('tool_' + role, root / f'demo-runtime/plugins/{role}/email-queue/__init__.py')
    queue = load('qtool_' + role, root / f'demo-runtime/plugins/{role}/email-queue/mail_queue.py')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(relay_runtime, 'current_turn', lambda: SimpleNamespace(
        closed=False, turn_id='one-turn', lease=SimpleNamespace(
            profile_key='test-profile', session_id='test-session', released=False)))
    store = queue.Store(tmp_path / 'mail-queue')
    lines = ['# Synthetic travel plan', '', '## Transport\n- Train option\n- Flight option', '',
             '## Highlights', '- Museum', '- Park', '', '| Day | Plan |', '| --- | --- |', '| 1 | Visit |']
    args = {'subject': 'Synthetic trip', 'body': lines}
    import jsonschema
    jsonschema.validate(args, plugin.SCHEMA['parameters'])
    # Model-provided multiline blocks must pass registration validation too.
    jsonschema.validate({'subject':'Ready plan', 'body':['# Plan\n\n- One\n- Two']}, plugin.SCHEMA['parameters'])
    with queue.Server(tmp_path / 'mail-queue/service.sock', store, lambda: ['reader@example.invalid']) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = json.loads(plugin.submit(args))
            assert response['status'] == 'queued'
            assert json.loads(plugin.submit(args))['status'] == 'duplicate'
            assert args['body'] == lines  # Conversion does not mutate recorded model arguments.
        finally:
            server.shutdown()
            thread.join(timeout=5)
    import sqlite3
    with sqlite3.connect(store.path) as db:
        body = db.execute('select body from jobs').fetchone()[0]
    assert body == normalize_email_body(lines)
    assert body.count('\n') == sum(block.count('\n') for block in lines) + len(lines) - 1
    _, html = render_email(body)
    assert '<h2' in html and '<ul' in html and '<table' in html


@pytest.mark.parametrize('role', ['cli', 'gateway', 'services'])
def test_email_completion_reports_actual_status(role):
    path = Path(__file__).resolve().parents[2] / f'demo-runtime/plugins/{role}/email-queue/__init__.py'
    spec = importlib.util.spec_from_file_location('finish_' + role, path)
    plugin = importlib.util.module_from_spec(spec);spec.loader.exec_module(plugin)
    def result(status, name='email_send'):
        return {'role':'tool','name':name,'content':json.dumps({'status':status,'job_id':'test-job'})}
    for status in ('queued','duplicate'):
        assert plugin.finish_email_turn(tool_results=[result(status)])['final_response'] == 'Your email has been queued for sending.'
    for status in ('rejected','unconfirmed'):
        outcome = plugin.finish_email_turn(tool_results=[result(status)])
        assert outcome['speak_final_response'] is True
        assert 'could not' in outcome['final_response']
    for status in ('sent','unknown'):
        assert plugin.finish_email_turn(tool_results=[result(status)]) is None
    assert plugin.finish_email_turn(tool_results=[result('queued','other_tool')]) is None
    assert plugin.finish_email_turn(tool_results=[result('queued'),result('queued')]) is None
    assert plugin.finish_email_turn(tool_results=[{'name':'email_send','content':'malformed'}]) is None
    assert plugin.finish_email_turn(tool_results=[{'name':'email_send','content':'{"status":"queued"}'}]) is None


def test_bullets_have_email_spacing_and_preserve_meaningful_numbers():
    plain, html = render_email('## Transport\n\n- **Train:** 4.5 hours\n- **Flight:** 2 hours')
    assert '<ol' not in html and 'list-style-type:disc' in html
    assert 'padding-left:24px' in html and 'margin:0 0 6px' in html
    assert '4.5 hours' in plain and '2 hours' in plain
