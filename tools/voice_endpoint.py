"""Optional local recording-end keyword detector; no agent/tool-loop policy."""
import logging
import math
from pathlib import Path
import queue
import re
import threading
import time

logger = logging.getLogger(__name__)
_hint_played = False
_hint_lock = threading.Lock()


def settings():
    from hermes_cli.config import load_config
    cfg = load_config()
    voice = cfg.get('voice') or {}
    raw = voice.get('end_phrase') if isinstance(voice, dict) else None
    if not isinstance(raw, dict) or raw.get('enabled') is not True:
        return None
    wake = cfg.get('wake_word') or {}
    sherpa = (wake.get('sherpa') or {}) if isinstance(wake, dict) else {}
    if not isinstance(sherpa, dict):
        sherpa = {}
    return {**raw, 'model_dir': raw.get('model_dir') or sherpa.get('model_dir'),
            'phrase': str(raw.get('phrase') or "That's all")}


def strip_end_phrase(text):
    """Strip only a terminal control phrase in opted-in voice transcription."""
    cfg = settings()
    if not cfg:
        return text
    # Old ending retained only as transcript cleanup during migration; it no
    # longer activates the keyword detector. Handle Whisper punctuation forms.
    phrase = cfg.get('phrase', "That's all")
    expression = (r"that(?:['’]?s|\s+is)[\s,\-]+all" if phrase.casefold() in {"that's all", 'that’s all'}
                  else re.escape(phrase).replace(r'\ ', r'\s+'))
    return re.sub(r'(?i)(?<!\w)(?:' + expression + r'|over[\s,\-]+and[\s,\-]+out)[\s.!?,;:…\"\'”’]*$', '', text).rstrip(' ,;:-')


def pending_hint_text():
    """Text for a combined first-wake cue; leave manual capture's file intact."""
    cfg = settings() or {}
    hint = cfg.get('hint_file')
    with _hint_lock:
        if _hint_played or not hint or not Path(hint).is_file():
            return None
    phrase = cfg.get('phrase') or "That's all"
    return f"Say {phrase} when you finish speaking."


def mark_hint_played():
    """Only call after the complete instruction finished without cancellation."""
    global _hint_played
    with _hint_lock:
        _hint_played = True


def prepare_recording_endpoint(recorder):
    """Warm before the recording cue; explain once with capture still paused."""
    global _hint_played
    prepare = getattr(recorder, 'prepare_endpoint', None)
    if not callable(prepare) or not prepare():
        return
    cfg = settings() or {}
    hint = cfg.get('hint_file')
    if not hint:
        return
    with _hint_lock:
        if _hint_played:
            return
        try:
            from tools.voice_mode import play_audio_file
            if not Path(hint).is_file():
                raise FileNotFoundError('Recording-end hint is not cached')
            play_audio_file(hint)
            time.sleep(0.65)
            _hint_played = True
        except Exception:
            logger.warning('Recording-end hint unavailable; recording remains usable')


class EndPhraseDetector:
    """Serial keyword worker fed by the recorder's own PCM, not another mic."""
    def __init__(self, cfg, engine=None):
        # Warm heavy imports before capture, not in the real-time worker.
        import numpy as np
        from scipy.signal import resample_poly
        self._np, self._resample_poly = np, resample_poly
        self.phrase = str(cfg.get('phrase') or "That's all")
        if engine is None:
            from tools.wake_word import _SherpaKwsEngine
            folder = Path(cfg.get('model_dir') or '')
            if not (folder / 'tokens.txt').is_file():
                raise RuntimeError('Local Sherpa model missing; no automatic download')
            engine = _SherpaKwsEngine({
                'phrase': self.phrase, 'profile_routing': False,
                'sensitivity': 0.5, 'sherpa': {
                    'model_dir': str(folder), 'aliases': [],
                    'max_active_paths': 16, 'keywords_threshold': 0.17}})
        self.engine = engine
        self._thread = None
        self._stop = threading.Event()
        self._queue = queue.Queue(maxsize=64)
        self.last_reason = None

    def start(self, callback, sample_rate, threshold):
        self.stop()
        if self._thread and self._thread.is_alive():
            raise RuntimeError('Prior recording-end worker is still stopping')
        self.engine.reset()
        self.callback, self.sample_rate, self.threshold = callback, sample_rate, threshold
        self._block_samples = max(1, round(sample_rate * .08))
        self._pending_chunks, self._pending_samples = [], 0
        self._queue = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self.last_reason = None
        self._thread = threading.Thread(target=self._run, daemon=True, name='recording-end-keyword')
        self._thread.start()

    def feed(self, pcm):
        if self._stop.is_set():
            return
        self._pending_chunks.append(pcm.copy())
        self._pending_samples += len(pcm)
        if self._pending_samples < self._block_samples:
            return
        combined = self._np.concatenate(self._pending_chunks, axis=0)
        consumed = 0
        try:
            while len(combined) - consumed >= self._block_samples:
                self._queue.put_nowait(combined[consumed:consumed+self._block_samples].copy())
                consumed += self._block_samples
        except queue.Full:
            self._stop.set()
            logger.warning('Recording-end detector overloaded (>5s audio backlog); using silence/time-limit fallback')
        remaining = combined[consumed:]
        self._pending_chunks = [remaining.copy()] if len(remaining) else []
        self._pending_samples = len(remaining)

    def _run(self):
        np = self._np
        resample_poly = self._resample_poly
        divisor = math.gcd(int(self.sample_rate), 16000)
        try:
            while not self._stop.is_set():
                try:
                    pcm = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                mono = np.asarray(pcm, dtype=np.float32)
                if mono.ndim > 1:
                    mono = mono.mean(axis=1)
                if self.sample_rate != 16000:
                    mono = resample_poly(mono, 16000 // divisor, int(self.sample_rate) // divisor)
                matched = self.engine.process(mono)
                if matched and not self._stop.is_set():
                    self.last_reason = 'end_phrase'
                    logger.info('Recording end phrase confirmed: %s', self.phrase)
                    self._stop.set()
                    self.callback()
        except Exception:
            self._stop.set()
            logger.warning('Recording-end detector failed; using silence/time-limit fallback', exc_info=True)

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def close(self):
        self.stop()
        if not self._thread or not self._thread.is_alive():
            self.engine.close()
