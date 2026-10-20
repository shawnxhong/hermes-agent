"""One-shot, response-scoped wake replay after successful TTS playback.

No audio is retained here. The sidecar timestamps the first recognition of each
wake candidate; replay is limited to the final configured second of this reply.
"""
import logging
import math
import time
import uuid

logger = logging.getLogger(__name__)


class WakeBuffer:
    def __init__(self, cli, seconds, stop):
        self.cli = cli
        self.seconds = seconds
        self.stop = stop
        self.owner = (cli.session_id, getattr(cli, "_scene_generation", 0))
        self.turn = uuid.uuid4().hex
        self.started_ms = time.monotonic() * 1000
        self.finished_ms = None
        self.consumed = False

    def complete(self, finished_ms):
        if not self.stop.is_set() and self.current():
            self.finished_ms = finished_ms

    def current(self):
        return (getattr(self.cli, "_voice_wake_buffer", None) is self
                and self.owner == (self.cli.session_id, getattr(self.cli, "_scene_generation", 0)))


def begin(cli, stop):
    """Start a reply scope only for the independently capturing wake sidecar."""
    cli._voice_wake_buffer = None
    from hermes_cli.config import load_config
    try:
        cfg = load_config()
        voice = cfg.get("voice") or {}
        wake = cfg.get("wake_word") or {}
        if not isinstance(voice, dict) or not isinstance(wake, dict):
            return None
        seconds = voice.get("wake_buffer_seconds", 1.0)
    except Exception:
        logger.debug("wake buffer settings unavailable", exc_info=True)
        return None
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or not 0 < seconds <= 2):
        return None
    if (not getattr(cli, "_voice_mode", False)
            or not getattr(cli, "_wake_word_active", False)
            or getattr(cli, "_voice_continuous", False)
            or wake.get("provider") != "external-asr"):
        return None
    state = WakeBuffer(cli, float(seconds), stop)
    cli._voice_wake_buffer = state
    return state


def take_replay(cli):
    """Consume one completed reply window; never bypass a genuinely busy CLI."""
    state = getattr(cli, "_voice_wake_buffer", None)
    if not isinstance(state, WakeBuffer) or state.consumed:
        return None
    if (not state.current() or state.stop.is_set()
            or getattr(cli, "_should_exit", False)
            or not getattr(cli, "_voice_mode", False)
            or not getattr(cli, "_wake_word_active", False)):
        state.consumed = True
        return None
    if (getattr(cli, "_agent_running", False)
            or getattr(cli, "_voice_recording", False)
            or getattr(cli, "_voice_processing", False)
            or getattr(cli, "_voice_continuous", False)
            or not cli._voice_tts_done.is_set()):
        return None
    if not cli._pending_input.empty():
        state.consumed = True
        return None
    end = state.finished_ms
    if end is None:
        return None
    state.consumed = True
    age = time.monotonic() * 1000 - end
    if not 0 <= age <= 5000:
        return None
    logger.info("wake_buffer ready seconds=%.3f", state.seconds)
    return {"start_ms": max(state.started_ms, end - state.seconds * 1000),
            "end_ms": end, "turn": state.turn,
            "echo_text": str(getattr(cli, "_voice_last_tts_text", ""))[-1024:]}
