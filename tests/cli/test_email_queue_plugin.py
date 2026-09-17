"""Real SQLite/socket/plugin paths; SMTP doubles cannot send external mail."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import smtplib
import threading
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]/'scripts/local-ovms/plugins/email-queue'

def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

queue = load('test_mail_queue',ROOT/'mail_queue.py')
plugin = load('test_mail_plugin',ROOT/'__init__.py')

def request(turn='t', body='Details', session='s'):
    return {'scope':['p',session,turn],'subject':'Trip','body':body}

@pytest.fixture
def store(tmp_path):
    return queue.Store(tmp_path/'mail')

@pytest.fixture
def short_home():
    with tempfile.TemporaryDirectory(prefix='mail-test-') as folder:
        yield Path(folder)

def states(store):
    with closing(store.connect()) as db:
        return [r[0] for r in db.execute('SELECT status FROM recipients ORDER BY address')]

def test_concurrent_rewrites_first_wins_and_next_turn_allowed(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda i:store.enqueue(request(body=str(i)),['a@example.test']),range(8)))
    assert sum(r['status']=='queued' for r in results)==1
    assert len({r['job_id'] for r in results})==1
    assert store.enqueue(request('next'),['a@example.test'])['status']=='queued'
    assert store.enqueue(request(session='im'),['a@example.test'])['status']=='queued'

@pytest.mark.parametrize('change',[
    {'subject':''},{'subject':'bad\nBcc: other@example.test'}, {'body':''},
    {'body':None},{'scope':['','','']},{'target':'other@example.test'},
])
def test_rejected_does_not_reserve_turn(store,change):
    with pytest.raises(ValueError):
        store.enqueue({**request(),**change},['a@example.test'])
    assert store.enqueue(request(),['a@example.test'])['status']=='queued'

def test_sent_not_replayed_after_restart(store):
    store.enqueue(request(),['a@example.test','b@example.test'])
    send=Mock(return_value=('sent',None,False))
    assert store.drain_one(send)
    assert send.call_count==2
    recovered=queue.Store(store.folder)
    recovered.recover()
    assert not recovered.drain_one(send)
    assert recovered.enqueue(request(body='rewritten'),['a@example.test'])['status']=='duplicate'
    assert states(store)==['sent','sent']

def test_connection_failure_stops_other_recipients(store):
    store.enqueue(request(),['a@example.test','b@example.test'])
    send=Mock(return_value=('failed','connection',True))
    store.drain_one(send)
    assert send.call_count==1 and states(store)==['failed','skipped']
    assert not store.drain_one(send)

def test_partial_and_unknown_never_retry(store):
    store.enqueue(request(),['a@example.test','b@example.test'])
    send=Mock(side_effect=[('sent',None,False),('unknown','lost_ack',True)])
    store.drain_one(send)
    assert states(store)==['sent','unknown']
    assert store.status()[0]['status']=='unknown'
    store.recover()
    assert not store.drain_one(send)

def test_crash_before_submission_conservatively_not_replayed(store):
    store.enqueue(request(),['a@example.test','b@example.test'])
    def crash(job,recipient):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        store.drain_one(crash)
    store.recover()
    assert states(store)==['unknown','skipped']
    assert not store.drain_one(Mock())

def test_queued_survives_restart(store):
    store.enqueue(request(),['a@example.test'])
    recovered=queue.Store(store.folder)
    recovered.recover()
    send=Mock(return_value=('sent',None,False))
    assert recovered.drain_one(send) and send.call_count==1

@pytest.mark.parametrize('error,expected',[(ConnectionError(),'unknown'),(smtplib.SMTPDataError(550,b'rejected'),'failed')])
def test_smtp_uncertainty_and_rejection(error,expected):
    smtp=Mock(); smtp.send_message.side_effect=error
    adapter=SimpleNamespace(_address='sender@example.test',_password='test',_connect_smtp=lambda:smtp)
    sender=queue.SMTPSender(adapter)
    result=sender({'subject':'Actual title','body':'Details'}, {'address':'a@example.test','message_id':'<fixed@test>'})
    assert result[0]==expected
    msg=smtp.send_message.call_args.args[0]
    assert msg['Subject']=='Actual title' and msg['Message-ID']=='<fixed@test>'

def test_smtp_acceptance_not_overridden_by_quit_failure():
    smtp=Mock(); smtp.send_message.return_value={}
    smtp.quit.side_effect=OSError(); smtp.close.side_effect=OSError()
    sender=queue.SMTPSender(SimpleNamespace(_address='s@test',_password='test',_connect_smtp=lambda:smtp))
    assert sender({'subject':'Title','body':'Body'},{'address':'a@test','message_id':'<id@test>'})[0]=='sent'

@pytest.mark.parametrize('platform',['cli','feishu','weixin'])
def test_plugin_real_socket_scope_and_fast_ack(short_home,monkeypatch,platform):
    from agent import relay_runtime
    home=short_home; monkeypatch.setenv('HERMES_HOME',str(home))
    store=queue.Store(home/'mail-queue')
    server=queue.Server(store.folder/'service.sock',store,lambda:['a@example.test'])
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    lease=relay_runtime.ConversationLease('p','session-'+platform,platform,None,None)
    turn=relay_runtime.RelayTurnContext(lease,'turn','task')
    token=relay_runtime._CURRENT_TURN.set(turn)
    try:
        start=time.monotonic()
        first=json.loads(plugin.submit({'subject':'Title','body':'Details'}))
        assert first['status']=='queued' and time.monotonic()-start<1
        assert json.loads(plugin.submit({'subject':'Other','body':'Rewrite'}))['status']=='duplicate'
        turn.closed=True
        assert json.loads(plugin.submit({'subject':'Title','body':'Details'}))['status']=='rejected'
        assert (store.folder/'service.sock').stat().st_mode & 0o777 == 0o600
    finally:
        relay_runtime._CURRENT_TURN.reset(token)
        server.shutdown(); server.server_close(); thread.join()

def test_missing_identity_fails_closed():
    from agent import relay_runtime
    token=relay_runtime._CURRENT_TURN.set(None)
    try:
        assert json.loads(plugin.submit({'subject':'Title','body':'Details'}))['status']=='rejected'
    finally:
        relay_runtime._CURRENT_TURN.reset(token)

def test_connection_failure_is_not_unknown():
    adapter=Mock(); adapter._address='s@test'; adapter._connect_smtp.side_effect=OSError()
    assert queue.SMTPSender(adapter)({'subject':'Title','body':'Body'},
        {'address':'a@test','message_id':'<id@test>'}) == ('failed','connection_or_auth_failure',True)

def test_no_default_recipient_does_not_claim_success(store):
    with pytest.raises(ValueError):
        store.enqueue(request(),[])
    assert store.status()==[]

def test_closed_client_does_not_undo_durable_enqueue(short_home):
    import socket
    store=queue.Store(short_home/'mail')
    path=store.folder/'service.sock'
    server=queue.Server(path,store,lambda:['a@test'])
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(path))
            client.sendall((json.dumps(request())+'\n').encode())
        deadline=time.monotonic()+1
        while not store.status() and time.monotonic()<deadline:
            time.sleep(0.01)
        assert len(store.status())==1
        assert store.enqueue(request(),['a@test'])['status']=='duplicate'
    finally:
        server.shutdown();server.server_close();thread.join()

def test_slow_smtp_does_not_block_enqueue(store):
    entered=threading.Event();release=threading.Event()
    store.enqueue(request(),['a@test'])
    def slow(job,recipient):
        entered.set();release.wait(3)
        return 'sent',None,False
    thread=threading.Thread(target=store.drain_one,args=(slow,));thread.start()
    try:
        assert entered.wait(1)
        start=time.monotonic()
        assert store.enqueue(request('another'),['a@test'])['status']=='queued'
        assert time.monotonic()-start<1
    finally:
        release.set();thread.join()

def test_unconfirmed_ipc_is_not_retried(monkeypatch):
    from agent import relay_runtime
    lease=relay_runtime.ConversationLease('p','s','cli',None,None)
    token=relay_runtime._CURRENT_TURN.set(relay_runtime.RelayTurnContext(lease,'t','task'))
    client=Mock();client.makefile.return_value.readline.side_effect=TimeoutError()
    manager=Mock();manager.__enter__=Mock(return_value=client);manager.__exit__=Mock(return_value=False)
    monkeypatch.setattr(plugin.socket,'socket',Mock(return_value=manager))
    try:
        assert json.loads(plugin.submit({'subject':'Title','body':'Body'}))['status']=='unconfirmed'
        assert client.sendall.call_count==1
    finally:
        relay_runtime._CURRENT_TURN.reset(token)
