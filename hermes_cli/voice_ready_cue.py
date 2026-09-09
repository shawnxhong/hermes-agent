"""Local, synchronous answer-window cue; no model or IM involvement."""
import logging
import math
import time

logger = logging.getLogger(__name__)


def settings():
    from hermes_cli.config import load_config
    from utils import is_truthy_value
    config = load_config()
    voice = config.get('voice') or {}
    raw = voice.get('ready_cue') if isinstance(voice, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    text = raw.get('intro_text', 'After the tone, you can answer directly.')
    if not isinstance(text, str) or not text.strip():
        text = 'After the tone, you can answer directly.'
    try:
        gap = float(raw.get('pre_gap_seconds', 0.65))
        gap = min(2.0, max(0.0, gap)) if math.isfinite(gap) else 0.65
    except (TypeError, ValueError):
        gap = 0.65
    return {'enabled': is_truthy_value(raw.get('enabled'), default=False),
            'pre_gap_seconds': gap,
            'intro_enabled': is_truthy_value(raw.get('intro_enabled'), default=True),
            'text': ' '.join(text.split()[:20])[:120], 'tts': config.get('tts') or {}}


def play_ready_cue(cli):
    """Finish playback before callers open capture. Retain legacy opt-out behavior."""
    from tools import voice_mode
    try:
        cfg = settings()
        if cfg['enabled']:
            # All callers arrive after question playback, before capture.
            announce_once(cli)
            # Separate the cue perceptually from the last TTS syllable.
            time.sleep(cfg.get('pre_gap_seconds', 0.65))
            # Existing bounded player provides click-free fades and user volume.
            voice_mode.play_beep(frequency=660, duration=0.20, count=1)
        elif cli._voice_beeps_enabled():
            voice_mode.play_beep(frequency=880, count=1)
    except Exception:
        logger.warning('Answer cue unavailable; continuing voice capture', exc_info=True)


def announce_once(cli):
    """Explain the first answer-window cue, after its question has played."""
    if getattr(cli, '_voice_ready_intro_done', False):
        return
    cfg = settings()
    if not getattr(cli, '_voice_tts', False):
        return
    if not cfg['enabled'] or not cfg['intro_enabled']:
        return
    try:
        from hermes_cli.voice_wake_ack import cached_audio
        from tools.voice_mode import play_audio_file
        path = cached_audio(cfg)
        if cli._voice_mode and not getattr(cli, '_should_exit', False):
            if play_audio_file(str(path)):
                cli._voice_ready_intro_done = True
    except Exception:
        logger.warning('Voice answer-cue introduction unavailable', exc_info=True)
