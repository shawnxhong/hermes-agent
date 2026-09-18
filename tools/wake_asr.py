"""Opt-in local ASR wake detector; bounded audio and one inference in flight."""
from collections import deque
import logging
import math
import queue
import re
import tempfile
import threading
import time
import wave

log = logging.getLogger(__name__)


def normalized_words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def matches_phrase(text, phrases):
    words = normalized_words(text)
    for phrase in phrases:
        wanted = normalized_words(phrase)
        if wanted and any(words[i:i + len(wanted)] == wanted
                          for i in range(len(words) - len(wanted) + 1)):
            return True
    return False


def local_transcriber():
    from hermes_cli.plugins import _ensure_plugins_discovered
    from agent.transcription_registry import get_provider
    _ensure_plugins_discovered()
    provider = get_provider('openvino_igpu')
    if provider is None or not provider.is_available():
        raise RuntimeError('ASR wake requires the local openvino_igpu service')
    return provider.transcribe  # Never automatic/cloud fallback.


class ASRWakeEngine:
    frame_length = 1280

    def __init__(self, cfg, *, transcribe=None):
        self._transcribe = transcribe or local_transcriber()
        settings = cfg.get('asr') or {}
        self.phrase = cfg.get('phrase', 'Hello Intel')
        self.phrases = [self.phrase, *settings.get('aliases', [])]
        self.rms = float(settings.get('rms_threshold', 180))
        self.silence_frames = math.ceil(float(settings.get('silence_seconds', .4)) / .08)
        self.max_frames = math.ceil(float(settings.get('max_seconds', 3.2)) / .08)
        self.min_frames = math.ceil(float(settings.get('min_speech_seconds', .16)) / .08)
        if not (self.rms > 0 and 1 <= self.silence_frames < self.max_frames <= 125
                and 1 <= self.min_frames < self.max_frames):
            raise ValueError('Invalid ASR wake segment settings')
        self._generation = 0
        self._closed = False
        self._busy = threading.Event()
        self._results = queue.Queue(maxsize=1)
        self.last_match = None
        self.reset()

    def _clear_audio(self):
        self._pre = deque(maxlen=3)
        self._frames = []
        self._speech = self._silence = 0

    def reset(self):
        self._generation += 1
        self._clear_audio()
        self.last_match = None

    def close(self):
        self._closed = True
        self.reset()

    def _recognize(self, frames, generation):
        started = time.monotonic()
        hit = False
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav') as audio:
                with wave.open(audio.name, 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(b''.join(frames))
                result = self._transcribe(audio.name, language='en')
            hit = bool(result.get('success') and
                       matches_phrase(result.get('transcript', ''), self.phrases))
            log.info('wake_asr seconds=%.3f matched=%s success=%s',
                     time.monotonic() - started, hit, result.get('success', False))
        except Exception as exc:
            log.warning('wake_asr failed: %s', type(exc).__name__)
        finally:
            # No transcript/audio logging; stale generations cannot wake the user.
            try:
                self._results.put_nowait((generation, hit))
            except queue.Full:
                pass
            self._busy.clear()

    def process(self, frame):
        import numpy as np
        if self._closed:
            return False
        try:
            generation, hit = self._results.get_nowait()
            if generation == self._generation and hit:
                self._clear_audio()
                self.last_match = (self.phrase, 'asr')
                return True
        except queue.Empty:
            pass
        pcm = np.asarray(frame, dtype=np.int16).reshape(-1)
        loud = bool(len(pcm) and np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) >= self.rms)
        raw = pcm.astype('<i2', copy=False).tobytes()
        if not self._frames:
            if not loud:
                self._pre.append(raw)
                return False
            self._frames = list(self._pre)
            self._pre.clear()
        self._frames.append(raw)
        self._speech += int(loud)
        self._silence = 0 if loud else self._silence + 1
        if self._silence >= self.silence_frames or len(self._frames) >= self.max_frames:
            frames = self._frames
            enough = self._speech >= self.min_frames
            self._clear_audio()
            if enough and not self._busy.is_set():
                self._busy.set()
                threading.Thread(target=self._recognize, args=(frames, self._generation),
                                 daemon=True, name='wake-asr').start()
        return False
