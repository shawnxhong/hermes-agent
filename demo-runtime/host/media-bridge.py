#!/usr/bin/env python3
"""Same-UID media broker. Stop advances a device epoch before waiting on play."""
import argparse
import fcntl
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import socketserver
import struct
import threading
import time
import uuid


class Broker:
    def __init__(self, root, player):
        self.root, self.player = root, player
        self.lock = threading.RLock()
        self.epoch_lock = threading.Lock()

    def epoch(self):
        try:
            return (self.root / 'epoch').read_text().strip()
        except FileNotFoundError:
            return '0'

    def advance(self):
        with self.epoch_lock:
            value = uuid.uuid4().hex
            path = self.root / 'epoch.tmp'
            path.write_text(value)
            path.replace(self.root / 'epoch')
            return value

    def speech_stopped(self):
        for path in (self.root / 'leases').glob('*.json'):
            try:
                item = json.loads(path.read_text())
            except FileNotFoundError:
                continue
            if item['epoch'] != self.epoch() and not item['cancelled']:
                # File locks survive container boundaries and release on crash.
                # A slow live process must NOT be mistaken for a dead one.
                try:
                    with open(path.with_suffix('.lock'), 'r') as owner:
                        try:
                            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            return False
                except FileNotFoundError:
                    pass
        return True

    def control(self, req):
        action = req['action']
        if action == 'stop':
            self.advance()
            with self.lock:
                result = self.player.control('stop')
            deadline = time.monotonic() + 3.2
            while not self.speech_stopped() and time.monotonic() < deadline:
                time.sleep(.025)
            if not self.speech_stopped():
                return {'success': False, 'error': 'Speech cancellation was not acknowledged.'}
            return result
        with self.lock:
            if action in ('play', 'mp3', 'mp4'):
                if req.get('epoch') != self.epoch():
                    return {'success': False, 'error': 'Playback request cancelled by a newer stop.'}
                result = self.player.control(action, req['target'])
                if req['epoch'] != self.epoch():
                    self.player.control('stop')
                    return {'success': False, 'error': 'Playback cancelled while starting.'}
                return result
            return self.player.control(action, req['target'])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--socket', type=Path, required=True)
    p.add_argument('--player', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    args.socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    assert args.socket.parent.stat().st_uid == os.getuid()
    assert args.socket.parent.stat().st_mode & 0o077 == 0
    lock = open(args.socket.parent / 'lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    spec = importlib.util.spec_from_file_location('host_media', args.player)
    player = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(player)
    broker = Broker(args.socket.parent, player)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            try:
                _, uid, _ = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                assert uid == os.getuid()
                raw = self.rfile.readline(4097)
                assert len(raw) <= 4096
                req = json.loads(raw)
                assert set(req) <= {'action', 'target', 'epoch'}
                assert req['action'] in ('list', 'play', 'mp3', 'mp4', 'stop', 'status')
                assert isinstance(req['target'], str) and len(req['target']) <= 1024
                assert isinstance(req.get('epoch', ''), str) and len(req.get('epoch', '')) <= 64
                result = broker.control(req)
            except Exception as exc:
                logging.exception('Media bridge request failed')
                result = {'success': False, 'error': str(exc) or type(exc).__name__}
            self.wfile.write((json.dumps(result) + '\n').encode())

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    if args.socket.exists():
        args.socket.unlink()
    with Server(str(args.socket), Handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
