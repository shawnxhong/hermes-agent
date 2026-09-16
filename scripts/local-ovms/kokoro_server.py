#!/usr/bin/env python3
"""Resident CPU Kokoro synthesis; playback remains exclusively in Hermes."""
import argparse
import fcntl
import io
import json
import logging
import os
import socket
import select
from pathlib import Path
import socketserver
import stat
import time

from kokoro_wire import receive, send

log = logging.getLogger('hermes.tts.kokoro')


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid request')
    text = value.get('text')
    speed = value.get('speed', 1.)
    voice = value.get('voice', 'af_maple')
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError('Text must contain 1..2000 characters')
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not .5 <= speed <= 2:
        raise ValueError('Invalid speech speed')
    if voice != 'af_maple':
        raise ValueError('This deployment uses af_maple')
    return text, speed, voice


class Engine:
    def __init__(self, folder, threads):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.add_session_config_entry('session.intra_op.allow_spinning', '0')
        options.add_session_config_entry('session.inter_op.allow_spinning', '0')
        self.model = Kokoro.from_session(
            ort.InferenceSession(str(folder/'kokoro-v1.1-zh.onnx'), sess_options=options,
                                 providers=['CPUExecutionProvider']),
            str(folder/'voices-v1.1-zh.bin'), vocab_config=str(folder/'config.json'))
        self({'text':'Ready.'})

    def __call__(self, value):
        import soundfile as sf
        text, speed, voice = validate(value)
        started = time.monotonic()
        samples, rate = self.model.create(text, voice=voice, speed=speed, lang='en-us')
        out = io.BytesIO()
        sf.write(out, samples, rate, format='WAV', subtype='PCM_16')
        log.info('tts_synthesis seconds=%.3f chars=%d',time.monotonic()-started,len(text))
        return out.getvalue()


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        try:
            body = receive(self.request, 16384)
            if select.select([self.request], [], [], 0)[0]:
                if self.request.recv(1, socket.MSG_PEEK) == b'':
                    return
            output = self.server.engine(json.loads(body)) if body else b'READY'
        except Exception as exc:
            log.warning('TTS request failed: %s',type(exc).__name__)
            output = b'ERROR'
        try:
            send(self.request, output)
        except OSError:
            pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--socket',type=Path,required=True)
    p.add_argument('--threads',type=int,default=4)
    args = p.parse_args()
    if not args.model_dir.is_dir() or not 1 <= args.threads <= 16:
        p.error('Existing model directory and 1..16 CPU threads required')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    args.socket.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    info = args.socket.parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        p.error('Socket directory must be owned by user with mode 0700')
    with open(str(args.socket)+'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.socket.exists() or args.socket.is_symlink():
            old = args.socket.lstat()
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.getuid():
                p.error('Refusing to replace non-owned/non-socket path')
            args.socket.unlink()
        engine = Engine(args.model_dir,args.threads)
        with socketserver.UnixStreamServer(str(args.socket),Handler) as server:
            os.chmod(args.socket,0o600)
            server.engine = engine
            log.info('ready device=CPU threads=%d',args.threads)
            try:
                server.serve_forever()
            finally:
                args.socket.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
