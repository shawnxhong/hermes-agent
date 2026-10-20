"""Recording-scoped host quiet lease; no model tools or audio in the protocol."""
import functools
import json
import logging
from pathlib import Path
import socket
import threading
import uuid

log = logging.getLogger(__name__)
SOCKET = Path('/ipc/recording-quiet/service.sock')


def validate_config(raw):
    if not isinstance(raw, dict) or set(raw) != {'enabled', 'fan', 'output', 'max_duration_seconds'}:
        raise ValueError('recording_quiet requires enabled, fan, output and max_duration_seconds')
    def boolean(value):
        if type(value) is not bool: raise ValueError('quiet enabled fields must be boolean')
    def number(value, low, high):
        if type(value) not in (int, float) or not low <= value <= high:
            raise ValueError(f'quiet value must be finite and between {low} and {high}')
    boolean(raw['enabled'])
    for name, keys in [('fan', {'enabled','control_percent','release_temperature_c'}),
                       ('output', {'enabled','max_volume_percent'})]:
        if not isinstance(raw[name], dict) or set(raw[name]) != keys:
            raise ValueError('Invalid recording_quiet.'+name)
        boolean(raw[name]['enabled'])
    number(raw['fan']['control_percent'], 2, 100)
    number(raw['fan']['release_temperature_c'], 40, 80)
    number(raw['output']['max_volume_percent'], 0, 100)
    number(raw['max_duration_seconds'], 1, 120)
    return raw


def request(action, token, path=SOCKET):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(.75)
        client.connect(str(path))
        client.sendall((json.dumps({'action': action, 'token': token})+'\n').encode())
        data = client.makefile('rb').readline(16384)
        result = json.loads(data)
        if not result.get('ok'): raise RuntimeError(result.get('error', 'quiet request failed'))
        return result


class Lease:
    def __init__(self, cli):
        self.cli = cli
        self.token = uuid.uuid4().hex
        self.generation = getattr(cli, '_scene_generation', 0)
        self.done = threading.Event()
        self.worker = None

    def start(self):
        try:
            result = request('acquire', self.token)
            if self.done.is_set():
                self.close()
                return
            if not result.get('active'):
                self.done.set()
                return
            self.worker = threading.Thread(target=self.watch, name='recording-quiet', daemon=True)
            self.worker.start()
        except Exception:
            self.done.set()
            log.warning('Recording quiet unavailable; capture continues', exc_info=True)

    def watch(self):
        while not self.done.wait(1):
            if (getattr(self.cli, '_should_exit', False)
                    or getattr(self.cli, '_scene_generation', 0) != self.generation
                    or not getattr(self.cli, '_voice_mode', True)):
                self.close()
                return
            try:
                if not request('renew', self.token).get('active'):
                    self.done.set()
                    return
            except Exception:
                # No re-acquisition after expiry/failure: host watchdog restores.
                self.close()
                return

    def close(self):
        self.done.set()
        try: request('release', self.token)
        except Exception: log.warning('Quiet release unavailable; host lease will expire')


def begin(cli):
    if not SOCKET.exists() or getattr(cli, "_scene_switching", False) or getattr(cli, "_should_exit", False):
        return None
    with cli._voice_lock:
        lease = getattr(cli, '_recording_quiet', None)
        if lease is not None and not lease.done.is_set(): return lease
        lease = Lease(cli)
        cli._recording_quiet = lease
    lease.start()
    return lease


def end(cli, expected=None):
    if getattr(cli, "_recording_quiet", None) is None: return
    with cli._voice_lock:
        lease = getattr(cli, '_recording_quiet', None)
        if expected is not None and lease is not expected: return
        cli._recording_quiet = None
    if lease is not None: lease.close()


def recording_start(function=None, *, after_cue=False):
    """Share a wake-ack lease with manual/continuous recording, release failures."""
    if function is None:
        return functools.partial(recording_start, after_cue=after_cue)
    @functools.wraps(function)
    def wrapped(cli, *args, **kwargs):
        lease = None if after_cue else begin(cli)
        try: return function(cli, *args, **kwargs)
        finally:
            if not getattr(cli, '_voice_recording', False): end(cli, lease)
    return wrapped
