#!/usr/bin/env python3
"""Resident English Whisper large-v3-turbo GPU worker. No cloud fallback."""
import argparse
import fcntl
import io
import json
import logging
import os
from pathlib import Path
import socketserver
import stat
import time

from transport import MAX_AUDIO_BYTES, receive, send

log = logging.getLogger('hermes.asr.igpu')


def transcribe_samples(audio, pipe, vad, collect):
    if len(audio) > 16000 * 180:
        raise ValueError('Audio exceeds the 180-second trial limit')
    if not len(audio):
        return {'success': True, 'transcript': '', 'no_speech': True}
    # Keep Silero speech gating; never send silence to Whisper. Bound chunks
    # at speech pauses so lengthy input cannot exhaust one decoder window.
    spans = vad(audio, min_silence_duration_ms=500, max_speech_duration_s=25)
    if not spans:
        return {'success': True, 'transcript': '', 'no_speech': True}
    chunks, _ = collect(audio, spans, max_duration=25)
    texts = []
    for chunk in chunks:
        if not len(chunk):
            continue
        result = pipe.generate(chunk, language='<|en|>', task='transcribe',
                               num_beams=1, max_new_tokens=440)
        texts.append(''.join(result.texts).strip())
    return {'success': True, 'transcript': ' '.join(t for t in texts if t)}


class Engine:
    def __init__(self, model):
        import numpy as np
        import openvino_genai
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import get_speech_timestamps, collect_chunks
        self.decode, self.vad, self.collect = decode_audio, get_speech_timestamps, collect_chunks
        self.pipe = openvino_genai.WhisperPipeline(str(model), 'GPU')
        # Compile before publishing readiness; no transcript is logged/used.
        blank = np.zeros(16000, dtype=np.float32)
        self.vad(blank)
        self.pipe.generate(blank, language='<|en|>', task='transcribe',
                           num_beams=1, max_new_tokens=1)

    def __call__(self, payload):
        start = time.monotonic()
        audio = self.decode(io.BytesIO(payload), sampling_rate=16000)
        result = transcribe_samples(audio, self.pipe, self.vad, self.collect)
        log.info('asr_complete seconds=%.3f audio_seconds=%.3f no_speech=%s',
                 time.monotonic()-start, len(audio)/16000, result.get('no_speech', False))
        return result


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        try:
            payload = receive(self.request, MAX_AUDIO_BYTES)
            result = (self.server.engine(payload) if payload else
                      {'success': True, 'ready': True, 'device': 'GPU', 'language': 'en'})
        except Exception as exc:
            log.warning('ASR request failed (%s)', type(exc).__name__)
            result = {'success': False, 'transcript': '',
                      'error': 'Local GPU transcription failed: ' + type(exc).__name__}
        try:
            send(self.request, json.dumps(result).encode())
        except (OSError, TimeoutError):
            pass  # Cancelled caller: do not replay or retain its audio.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--socket', type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    if not args.model.is_dir():
        parser.error('Model must be an existing local model directory')
    args.socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = args.socket.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
        parser.error('Socket parent must be owned by this user with mode 0700')
    with open(str(args.socket) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.socket.exists() or args.socket.is_symlink():
            info = args.socket.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                parser.error('Refusing to replace non-owned/non-socket path')
            args.socket.unlink()
        engine = Engine(args.model)
        with socketserver.UnixStreamServer(str(args.socket), Handler) as server:
            os.chmod(args.socket, 0o600)
            server.engine = engine
            log.info('ready device=GPU language=en beam=1')
            try:
                server.serve_forever()
            finally:
                args.socket.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
