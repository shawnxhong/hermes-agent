#!/usr/bin/env python3
"""Lightweight resident TTS client with one explicit legacy fallback."""
import argparse
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from kokoro_wire import receive, send


def synthesize(path, text, speed=1., voice='af_maple', timeout=20):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        send(sock,json.dumps({'text':text,'speed':speed,'voice':voice}).encode())
        wav = receive(sock,16*1024*1024)
    if not wav.startswith(b'RIFF') or wav[8:12] != b'WAVE':
        raise ValueError('Resident TTS did not return WAV audio')
    return wav


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket',required=True)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--speed',type=float,default=1.)
    p.add_argument('--english-voice',default='af_maple')
    p.add_argument('--fallback-python')
    p.add_argument('--model-dir')
    args = p.parse_args()
    # Cancelled requests must exit, not enter the fallback path.
    signal.signal(signal.SIGTERM,lambda *_: sys.exit(143))
    signal.signal(signal.SIGINT,lambda *_: sys.exit(130))
    started = time.monotonic()
    try:
        wav = synthesize(args.socket,args.input.read_text(),args.speed,args.english_voice)
        args.output.write_bytes(wav)
        print('tts_client source=resident seconds=%.3f' % (time.monotonic()-started),file=sys.stderr)
    except (OSError, ValueError) as exc:
        if not args.fallback_python or not args.model_dir:
            raise
        print('tts_client source=legacy_fallback reason='+type(exc).__name__,file=sys.stderr)
        subprocess.run([args.fallback_python,str(Path(__file__).with_name('kokoro_tts.py')),
                        '--model-dir',args.model_dir,'--input',str(args.input),
                        '--output',str(args.output),'--english-voice',args.english_voice,
                        '--speed',str(args.speed)],check=True,timeout=45)


if __name__ == '__main__':
    main()
