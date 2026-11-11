#!/usr/bin/env python3
"""Bounded recording leases. User service owns volume; narrow helper owns fan."""
import argparse
import copy
import json
import logging
import math
import os
from pathlib import Path
import signal
import socket
import struct
import threading
import subprocess
import time
import yaml
from recording_quiet import validate_config

log = logging.getLogger('recording-quiet')


def atomic(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    tmp.chmod(0o600)
    tmp.replace(path)


def rpc(path, data):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(.6)
        s.connect(str(path))
        s.sendall((json.dumps(data)+'\n').encode())
        result = json.loads(s.makefile('rb').readline(16384))
        if not result.get('ok'): raise RuntimeError(result.get('error'))
        return result


class Volume:
    def __init__(self, state, command=None):
        self.state = state
        self.command = command or (lambda *args: subprocess.check_output(args, text=True, timeout=1).strip())
        self.saved = json.loads(state.read_text()) if state.exists() else None
        self.restore()

    def sinks(self):
        return {s['name']:s for s in json.loads(self.command('pactl','--format=json','list','sinks'))}

    @staticmethod
    def values(s): return [c['value'] for c in s['volume'].values()]

    def restore(self):
        if not self.saved: return
        sink = self.sinks().get(self.saved['sink'])
        if sink and self.values(sink) == self.saved['last']:
            self.command('pactl','set-sink-volume',self.saved['sink'], *map(str,self.saved['desired']))
        # We never modify mute, and never overwrite a user's concurrent volume.
        log.info('volume_released sink=%s',self.saved['sink'])
        self.saved = None
        self.state.unlink(missing_ok=True)

    def apply(self, percent):
        name = self.command('pactl','get-default-sink')
        if self.saved and name != self.saved['sink']: self.restore()
        sink = self.sinks().get(name)
        if sink is None: raise RuntimeError('Default output disappeared')
        values = self.values(sink)
        if self.saved is None:
            self.saved = {'sink':name, 'desired':values, 'last':values}
        elif values != self.saved['last']:
            self.saved['desired'] = values  # User changed volume during recording.
        target = self.saved['desired']
        ceiling = int(65536*percent/100)
        peak = max(target)
        target = [round(v*ceiling/peak) for v in target] if peak > ceiling else target[:]
        self.saved['last'] = target
        atomic(self.state, self.saved)  # Crash recovery snapshot before mutation.
        if values != target:
            self.command('pactl','set-sink-volume',name,*map(str,target))
            log.info('volume_limited sink=%s percent=%s',name,percent)


class Fan:
    def __init__(self, state, root=Path('/fan'), temperatures=Path('/temps')):
        self.state, self.root, self.temperatures = state, root, temperatures
        self.original = None
        self.blocked = False
        self.last_sample = 0
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if state.exists():
            old = json.loads(state.read_text())
            if old['boot'] == self.boot: self.original = old['original']; self._restore()
            else: state.unlink()

        self.desired = None
        self.condition = threading.Condition()
        self.closed = False
        self.worker = threading.Thread(target=self.run, name='fan-hardware', daemon=True)
        self.worker.start()

    def run(self):
        while True:
            with self.condition:
                desired = copy.deepcopy(self.desired)
                closed = self.closed
            try:
                if desired is None or closed: self._restore()
                else: self._apply(desired)
            except Exception: log.exception('fan_worker_failed')
            if closed: return
            with self.condition: self.condition.wait(timeout=1)

    def restore(self):
        with self.condition:
            self.desired = None
            self.condition.notify_all()

    def apply(self, config):
        with self.condition:
            if self.desired != config:
                self.desired = copy.deepcopy(config)
                self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.worker.join(timeout=10)

    def temperature(self):
        values = [int(p.read_text())/1000 for p in self.temperatures.glob('hwmon/*/temp*_input')]
        if not values or any(not 0 < t < 120 for t in values): raise RuntimeError('Temperature unavailable')
        return max(values)

    def _restore(self):
        if self.original is not None:
            (self.root/'state').write_text(str(self.original))
            log.info('fan_released original_state=%s rpm=%s',self.original,(self.root/'rpm').read_text().strip())
            self.original = None
            self.state.unlink(missing_ok=True)

    def begin(self): self.blocked = False

    def _apply(self, config):
        if self.blocked: return
        try:
            if (self.root/'type').read_text().strip() != 'TFN1': raise RuntimeError('Unsupported fan')
            temp = self.temperature()
            if temp >= config['release_temperature_c']: raise RuntimeError('Thermal override')
            maximum = int((self.root/'maximum').read_text())
            target = max(1, min(maximum, round(maximum*config['control_percent']/100)))
            if self.original is None:
                self.original = int((self.root/'state').read_text())
                atomic(self.state, {'original':self.original, 'boot':self.boot})
            # Firmware skips repeated FSLV; lower adjacent state forces reissue.
            (self.root/'state').write_text(str(target-1))
            (self.root/'state').write_text(str(target))
            if time.monotonic()-self.last_sample >= 5:
                log.info('fan_requested state=%s rpm=%s temperature=%s effect=unverified',
                         target,(self.root/'rpm').read_text().strip(),temp)
                self.last_sample = time.monotonic()
        except Exception:
            self.blocked = True
            self._restore()
            log.exception('fan_restriction_released')


class Controller:
    def __init__(self, config, volume=None, fan=None, fan_socket=None, clock=time.monotonic):
        self.config_path, self.volume, self.fan, self.fan_socket = config, volume, fan, fan_socket
        self.clock = clock
        self.token = None
        self.expires = self.deadline = 0
        self.config = None
        self.last_tick = 0
        self.retired = []
        self.fan_active = False

    def fan_call(self, action):
        if self.fan_socket is None: return
        try:
            result = rpc(self.fan_socket, {'action':action,'token':self.token, 'config':self.config})
            self.fan_active = result.get('active', False)
        except Exception:
            self.fan_active = False
            log.warning('fan_helper_unavailable action=%s',action)

    def release(self, reason):
        if self.token is None: return
        token = self.token
        # Independent restore attempts: a failure in one must not skip the other.
        try:
            if self.volume: self.volume.restore()
        except Exception: log.exception('volume_restore_failed; retry on next tick')
        try:
            if self.fan: self.fan.restore()
            elif self.fan_active: self.fan_call('release')
        except Exception: log.exception('fan_restore_failed; retry on next tick')
        self.retired = (self.retired+[token])[-1024:]
        self.token = None
        log.info('lease_released token=%s reason=%s',token,reason)

    def handle(self, data):
        token, action = data.get('token'), data.get('action')
        if not isinstance(token,str) or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):
            raise ValueError('Invalid lease token')
        if action not in ('acquire','renew','release'): raise ValueError('Invalid action')
        now = self.clock()
        if self.token and now >= min(self.expires,self.deadline): self.release('expired')
        if action == 'release':
            if token == self.token: self.release('client')
        elif action == 'renew':
            if token == self.token: self.expires = min(now+5,self.deadline)
        elif token not in self.retired and token != self.token:
            raw = data.get('config') if self.fan else yaml.safe_load(self.config_path.read_text())['recording_quiet']
            cfg = validate_config(raw)
            if not cfg['enabled']: return {'ok':True,'active':False}
            self.release('replaced')
            self.config = copy.deepcopy(cfg)
            self.token = token
            self.expires, self.deadline = now+5, now+cfg['max_duration_seconds']
            if self.fan: self.fan.begin()
            elif cfg['fan']['enabled']: self.fan_call('acquire')
            log.info('lease_acquired token=%s config=%s',token,cfg)
            self.tick(force=True)
        return {'ok':True,'active': self.token == token}

    def tick(self, force=False):
        now = self.clock()
        if self.token and now >= min(self.expires,self.deadline): self.release('expired')
        if self.token is None:
            # Retry interrupted restores; never reassert a restriction here.
            for hardware in (self.volume,self.fan):
                if hardware:
                    try: hardware.restore()
                    except Exception: log.exception('restore_retry_failed')
            return
        if not force and now-self.last_tick < (1 if self.fan else .25): return
        self.last_tick = now
        if self.volume and self.config['output']['enabled']:
            try: self.volume.apply(self.config['output']['max_volume_percent'])
            except Exception: log.exception('volume_limit_failed')
        if self.fan and self.config['fan']['enabled']: self.fan.apply(self.config['fan'])
        elif self.fan_active and now-getattr(self,'last_fan_renew',0)>=1:
            self.fan_call('renew'); self.last_fan_renew=now


def serve(path, controller, uid):
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig,stop)
    path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path)); path.chmod(0o660); server.listen(8); server.settimeout(.2)
        try:
            while not stopping:
                controller.tick()
                try: client,_ = server.accept()
                except socket.timeout: continue
                with client:
                    client.settimeout(.5)
                    try:
                        _, peer_uid,_ = struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                        if peer_uid != uid: raise PermissionError('Peer UID denied')
                        raw = client.makefile('rb').readline(16384)
                        if not raw.endswith(b'\n'): raise ValueError('Oversized/incomplete request')
                        result = controller.handle(json.loads(raw))
                    except Exception as exc:
                        log.warning('request_failed %s',exc)
                        result = {'ok':False,'error':str(exc)}
                    try: client.sendall((json.dumps(result)+'\n').encode())
                    except OSError: pass
        finally:
            controller.release('shutdown')
            if controller.fan: controller.fan.close()
            path.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--role',choices=['user','fan'],required=True)
    p.add_argument('--socket',type=Path,required=True)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--config',type=Path)
    p.add_argument('--fan-socket',type=Path)
    p.add_argument('--uid',type=int,default=1000)
    a=p.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    os.umask(0o077)
    if a.role=='fan': c=Controller(None,fan=Fan(a.state))
    else: c=Controller(a.config,volume=Volume(a.state),fan_socket=a.fan_socket)
    serve(a.socket,c,a.uid)

if __name__=='__main__': main()