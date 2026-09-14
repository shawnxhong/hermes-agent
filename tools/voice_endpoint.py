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
            'phrase': 'Over and out'}


def strip_end_phrase(text):
    """Strip only a terminal control phrase in opted-in voice transcription."""
    if not settings():
        return text
    return re.sub(r'(?i)(?<!\w)over[\s,\-]+and[\s,\-]+out[\s.!?,;:…]*$', '', text).rstrip(' ,;:-')


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
        if engine is None:
            from tools.wake_word import _SherpaKwsEngine
            folder = Path(cfg.get('model_dir') or '')
            if not (folder / 'tokens.txt').is_file():
                raise RuntimeError('Local Sherpa model missing; no automatic download')
            engine = _SherpaKwsEngine({
                'phrase': 'Over and out', 'profile_routing': False,
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
        self._queue = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self.last_reason = None
        self._thread = threading.Thread(target=self._run, daemon=True, name='recording-end-keyword')
        self._thread.start()

    def feed(self, pcm):
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait(pcm.copy())
        except queue.Full:
            self._stop.set()
            logger.warning('Recording-end detector overloaded; using silence/time-limit fallback')

    def _run(self):
        import numpy as np
        from scipy.signal import resample_poly
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
                    logger.info('Recording end phrase confirmed: Over and out')
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
