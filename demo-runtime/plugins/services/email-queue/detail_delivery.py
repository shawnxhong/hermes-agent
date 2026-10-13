"""Outbound-only detail queue. No Gateway, LLM, channel fallback or send retries."""
import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import threading
import urllib.error
import urllib.request
import uuid

from mail_queue import Store, Server, production_settings


class DetailStore(Store):
    def validate_addresses(self, addresses):
        if len(addresses) == 1 and re.fullmatch(r'feishu:oc_[a-zA-Z0-9]+', addresses[0]):
            return
        super().validate_addresses(addresses)

    def enqueue(self, request, addresses):
        # Reject invalid Unicode before SQLite/HTTP; do not alter the user's text.
        json.dumps(request, ensure_ascii=False).encode('utf-8')
        result = super().enqueue(request, addresses)
        # A duplicate's destination comes from its original immutable job, not
        # today's config. One accepted default-details job per original turn.
        with closing(self.connect()) as db:
            target = db.execute('SELECT address FROM recipients WHERE job=? LIMIT 1',
                                (result['job_id'],)).fetchone()[0]
        result['channel'] = 'feishu' if target.startswith('feishu:') else 'email'
        result['instruction'] = ('Details queued, not delivered. Give one brief summary; '
                                 'do not poll, retry or claim arrival.' if result['status'] == 'queued'
                                 else 'No new details queued. Do not retry.')
        return result


def settings(home):
    import yaml
    config = yaml.safe_load((home/'config.yaml').read_text()) or {}
    detail = config.get('detail_delivery', {})
    channel = detail.get('channel', 'email')
    if channel == 'feishu':
        target = detail.get('feishu', {})
        if target.get('target_type', 'chat_id') != 'chat_id':
            raise ValueError('Only a configured Feishu group is supported')
        chat = target.get('target_id', '')
        if not isinstance(chat, str) or not re.fullmatch(r'oc_[a-zA-Z0-9]+', chat):
            raise ValueError('Feishu group missing')
        if not os.environ.get('FEISHU_APP_ID') or not os.environ.get('FEISHU_APP_SECRET'):
            raise ValueError('Feishu credentials missing')
        return ['feishu:' + chat]
    if channel == 'email':
        _, addresses = production_settings(home)
        return addresses()
    raise ValueError('Unsupported detail delivery channel')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api(path, payload, token=None):
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request('https://open.feishu.cn/open-apis/' + path,
                                    data=json.dumps(payload).encode(), headers=headers)
    # Do not follow redirects with credentials, retry, or log response bodies.
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        return json.loads(response.read(1024 * 1024))


def chunks(text, limit=4000):
    """UTF-8 byte bounded chunks; preserve every character in the original text."""
    result, current, size = [], [], 0
    for char in text:
        width = len(char.encode('utf-8'))
        if size + width > limit:
            result.append(''.join(current)); current, size = [], 0
        current.append(char); size += width
    if current:
        result.append(''.join(current))
    return result


class Sender:
    def __init__(self, home, store, call=api, mail_factory=production_settings):
        self.home, self.store, self.call, self.mail_factory = home, store, call, mail_factory
        with closing(store.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS parts (job TEXT, part INTEGER, '
                       'status TEXT, message_id TEXT, PRIMARY KEY(job,part))')
            db.execute("UPDATE parts SET status='unknown' WHERE status='sending'")

    def record(self, job, index, status, message_id=None):
        with closing(self.store.connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO parts VALUES (?,?,?,?)',
                       (job, index, status, message_id))

    def __call__(self, job, recipient):
        if not recipient['address'].startswith('feishu:'):
            try:
                sender, _ = self.mail_factory(self.home)
            except Exception:
                return 'failed', 'email_configuration_unavailable', True
            return sender(job, recipient)
        try:
            auth = self.call('auth/v3/tenant_access_token/internal',
                             {'app_id': os.environ['FEISHU_APP_ID'],
                              'app_secret': os.environ['FEISHU_APP_SECRET']})
            token = auth.get('tenant_access_token')
            if auth.get('code') != 0 or not isinstance(token, str) or not token:
                return 'failed', 'feishu_auth_rejected', True
        except Exception:
            return 'failed', 'feishu_auth_unavailable', True
        texts = chunks(job['subject'] + '\n\n' + job['body'])
        for index, text in enumerate(texts):
            self.record(job['id'], index, 'sending')
            try:
                result = self.call('im/v1/messages?receive_id_type=chat_id', {
                    'receive_id': recipient['address'].removeprefix('feishu:'),
                    'msg_type': 'text',
                    'content': json.dumps({'text': f'[{index+1}/{len(texts)}] ' + text}
                                          if len(texts) > 1 else {'text': text}, ensure_ascii=False),
                    'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, job['id'] + ':' + str(index)))}, token)
                if result.get('code') != 0:
                    self.record(job['id'], index, 'failed')
                    return ('unknown' if index else 'failed'), 'feishu_rejected_or_partial', True
                message_id = result.get('data', {}).get('message_id')
                if not isinstance(message_id, str) or not message_id:
                    raise ValueError('Missing message acknowledgement')
                self.record(job['id'], index, 'sent', message_id)
            except Exception:
                self.record(job['id'], index, 'unknown')
                return 'unknown', 'feishu_outcome_unknown', True
        return 'sent', None, False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    os.environ['HERMES_HOME'] = str(args.home)
    from dotenv import load_dotenv
    load_dotenv(args.home/'.env')
    store = DetailStore(args.home/'detail-queue')
    lock = open(store.folder/'service.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    store.recover()
    sender = Sender(args.home, store)
    path = store.folder/'service.sock'
    path.unlink(missing_ok=True)
    stop = threading.Event()
    def worker():
        while not stop.is_set():
            try:
                if not store.drain_one(sender): stop.wait(0.25)
            except Exception:
                os._exit(1)
    with Server(path, store, lambda: settings(args.home)) as server:
        threading.Thread(target=worker, daemon=True).start()
        def shutdown(*unused):
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        server.serve_forever(poll_interval=0.2)
    path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
