"""Device-wide output cancellation; never changes capture or audio routing.

The host bridge advances an epoch in the shared IPC volume. Operations register
before synthesis, so late synthesis/queued output cannot revive cancelled speech.
"""
import contextlib
import contextvars
import functools
import fcntl
import inspect
import json
import os
from pathlib import Path
import socket
import threading
import time
import uuid

_scope = contextvars.ContextVar('playback_epoch', default=None)


def root():
    return Path(os.environ.get('HERMES_MEDIA_BRIDGE', '/ipc/media/service.sock')).parent


def epoch():
    try:
        return (root() / 'epoch').read_text().strip()
    except FileNotFoundError:
        return '0'


def request(action, target='', expected=None):
    payload = {'action': action, 'target': target, 'epoch': epoch() if expected is None else expected}
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect(str(root() / 'service.sock'))
        client.sendall((json.dumps(payload) + '\n').encode())
        return json.loads(client.makefile('rb').readline(65536))


@contextlib.contextmanager
def output_guard(stop_event=None):
    # Outside the Docker media deployment preserve existing behavior.
    if not (root() / 'service.sock').exists():
        yield stop_event
        return
    from tools.voice_mode import stop_playback
    event = stop_event if stop_event is not None else threading.Event()
    version = getattr(event, '_hermes_playback_epoch', None) or _scope.get() or epoch()
    event._hermes_playback_epoch = version
    token = _scope.set(version)
    directory = root() / 'leases'
    directory.mkdir(mode=0o700, exist_ok=True)
    lease = directory / (uuid.uuid4().hex + '.json')
    ownership = open(lease.with_suffix('.lock'), 'w')
    fcntl.flock(ownership, fcntl.LOCK_EX)
    done = threading.Event()

    def publish(cancelled=False):
        tmp = lease.with_suffix('.tmp')
        tmp.write_text(json.dumps({'epoch': version, 'cancelled': cancelled, 'updated': time.time()}))
        tmp.replace(lease)

    def check():
        if epoch() != version:
            event.set()
            stop_playback()
            publish(True)
            return True
        publish()
        return False

    publish()
    cancelled = epoch() != version or event.is_set()
    if cancelled:
        event.set()
        publish(True)

    def watch():
        while not done.wait(.025):
            try:
                if check():
                    return
            except OSError:
                event.set()  # fail closed on lost cancellation channel
                stop_playback()
                return

    worker = threading.Thread(target=watch, daemon=True, name='playback-cancel')
    if not cancelled:
        worker.start()
    try:
        yield event
    finally:
        done.set()
        if worker.ident:
            worker.join(timeout=2)
        lease.unlink(missing_ok=True)
        ownership.close()
        lease.with_suffix('.lock').unlink(missing_ok=True)
        _scope.reset(token)


def cancellable_output(function):
    """Capture cancellation before generation and cover both TTS implementations."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        with output_guard(bound.arguments.get('stop_event')) as event:
            if event is not None and event.is_set():
                done = bound.arguments.get('tts_done_event')
                if done is not None:
                    done.set()
                return True
            if 'stop_event' in signature.parameters and event is not None:
                bound.arguments['stop_event'] = event
            return function(*bound.args, **bound.kwargs)
    return wrapped
