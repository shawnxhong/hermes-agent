#!/usr/bin/env python3
"""Offline, opt-in ASR microbenchmark; never changes the running service.

Use mono PCM16 16 kHz WAVs. The optional JSON manifest contains objects with
path and reference keys. Transcripts are printed only with --show-text.
OpenVINO measurements are an inference prototype (no production VAD wrapper);
passing a timing test alone must not select a production backend.
"""
import argparse
import json
import re
import statistics
import time
import wave
from pathlib import Path

import numpy as np


def word_errors(reference, hypothesis):
    ref = re.findall(r"[\w']+", reference.lower())
    hyp = re.findall(r"[\w']+", hypothesis.lower())
    previous = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        current = [i]
        for j, other in enumerate(hyp, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (word != other)))
        previous = current
    return previous[-1], len(ref)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', choices=['faster-whisper', 'openvino'], required=True)
    p.add_argument('--model', required=True, help='Existing local model directory; no downloads')
    p.add_argument('--device', default='cpu')
    p.add_argument('--compute-type', default='auto')
    p.add_argument('--threads', type=int, default=0)
    p.add_argument('--beams', type=int, nargs='+', default=[5])
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--manifest', type=Path)
    p.add_argument('--show-text', action='store_true')
    p.add_argument('audio', nargs='*', type=Path)
    args = p.parse_args()
    if not Path(args.model).is_dir():
        p.error('Model must be an existing local directory')
    samples = json.loads(args.manifest.read_text()) if args.manifest else [
        {'path': str(path)} for path in args.audio]
    if not samples or args.rounds < 1 or any(b < 1 for b in args.beams):
        p.error('Supply audio, positive rounds and positive beams')
    prepared = []
    for sample in samples:
        start = time.perf_counter()
        with wave.open(sample['path']) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                p.error('Audio must be mono PCM16 at 16 kHz')
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32) / 32768
        prepared.append((sample, audio))
        print(json.dumps({'stage': 'prepare', 'sample': Path(sample['path']).name,
                          'seconds': time.perf_counter() - start, 'audio_seconds': len(audio)/16000}), flush=True)
    start = time.perf_counter()
    if args.backend == 'faster-whisper':
        from faster_whisper import WhisperModel
        model = WhisperModel(args.model, device=args.device,
                             compute_type=args.compute_type, cpu_threads=args.threads,
                             local_files_only=True)
    else:
        import openvino_genai
        model = openvino_genai.WhisperPipeline(args.model, args.device.upper())
    print(json.dumps({'stage': 'load', 'seconds': time.perf_counter()-start,
                      'backend': args.backend, 'device': args.device}), flush=True)
    for beam in args.beams:
        times, errors, words = [], 0, 0
        # Round zero is cold/warmup and is reported separately, not hidden.
        for round_id in range(args.rounds + 1):
            for sample, audio in prepared:
                start = time.perf_counter()
                if args.backend == 'faster-whisper':
                    segments, info = model.transcribe(audio, language='en', beam_size=beam,
                        vad_filter=True, vad_parameters={'min_silence_duration_ms': 500},
                        condition_on_previous_text=False, no_speech_threshold=.6,
                        log_prob_threshold=-1.)
                    prepared_at = time.perf_counter()
                    text = ' '.join(s.text.strip() for s in segments
                                    if not (s.no_speech_prob > .6 and s.avg_logprob < -1.))
                else:
                    prepared_at = start
                    result = model.generate(audio, language='<|en|>', task='transcribe',
                                            num_beams=beam, max_new_tokens=256)
                    text = ''.join(result.texts).strip()
                elapsed = time.perf_counter() - start
                row = {'stage': 'decode', 'round': round_id, 'beam': beam,
                       'sample': Path(sample['path']).name, 'seconds': elapsed,
                       'frontend_seconds': prepared_at-start,
                       'generation_seconds': time.perf_counter()-prepared_at}
                if args.show_text:
                    row['text'] = text
                if 'reference' in sample:
                    e, n = word_errors(sample['reference'], text)
                    row.update(word_errors=e, reference_words=n)
                    if round_id:
                        errors += e
                        words += n
                if round_id:
                    times.append(elapsed)
                print(json.dumps(row), flush=True)
        print(json.dumps({'stage': 'summary', 'beam': beam, 'n': len(times),
                          'median': statistics.median(times), 'p95': float(np.percentile(times, 95)),
                          'wer': errors/words if words else None}), flush=True)


if __name__ == '__main__':
    main()
