"""One bounded same-session ASR window after an ordinary spoken question."""
import logging
import math
import threading
import time
from pathlib import Path
from hermes_cli.voice_response_policy import prepare_voice_tts_text

logger = logging.getLogger(__name__)


def settings():
    from hermes_cli.config import load_config
    from utils import is_truthy_value
    voice = load_config().get("voice") or {}
    raw = (voice.get("followup") or {}) if isinstance(voice, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    result = {"enabled": is_truthy_value(raw.get("enabled"), default=False)}
    for name, default, maximum in (("timeout_seconds", 30, 60),
                                   ("resume_seconds", 120, 600),
                                   ("playback_timeout_seconds", 120, 180)):
        try:
            value = float(raw.get(name, default))
            result[name] = min(maximum, max(1, value)) if math.isfinite(value) else default
        except (TypeError, ValueError):
            result[name] = default
    return result


def cancel_followup(cli):
    cli._voice_followup_resume = None
    event = getattr(cli, "_voice_followup_cancel", None)
    if event is not None:
        event.set()


def resume_question_session(cli):
    pending = getattr(cli, "_voice_followup_resume", None)
    cli._voice_followup_resume = None
    return bool(pending and pending[0] == cli.session_id and time.monotonic() < pending[1])


def start_followup(cli, response, *, voice_input, make_message):
    spoken = prepare_voice_tts_text(response or "")
    if not voice_input or not spoken:
        return False
    cfg = settings()
    if not cfg["enabled"] or not getattr(cli, "_voice_tts", False):
        return False
    with cli._voice_lock:
        if (not cli._voice_mode or cli._voice_continuous or cli._voice_recording
                or cli._voice_processing or cli._agent_running
                or getattr(cli, "_last_turn_interrupted", False)
                or not cli._pending_input.empty()):
            return False
        cancel_followup(cli)
        # A fresh wake shortly after an answer is a continuation too. Only
        # questions auto-open the microphone; statements merely retain context.
        cli._voice_followup_resume = (cli.session_id, time.monotonic() + cfg["resume_seconds"])
        if not spoken.endswith(("?", "？")):
            return False
        cli._voice_processing = True
        cancel = threading.Event()
        cli._voice_followup_cancel = cancel
        session = cli.session_id
        cli._voice_followup_resume = (session, time.monotonic() + cfg["resume_seconds"])
    thread = threading.Thread(target=_capture_followup,
        args=(cli, spoken, session, cancel, cfg, make_message),
        daemon=True, name="voice-followup")
    try:
        thread.start()
    except Exception:
        cli._voice_processing = False
        cli._voice_followup_resume = None
        raise
    return True


def _capture_followup(cli, spoken, session, cancel, cfg, make_message):
    from tools import voice_mode
    wav = None

    def cancelled():
        return (cancel.is_set() or getattr(cli, "_should_exit", False)
                or not cli._voice_mode or cli.session_id != session
                or cli._agent_running or not cli._pending_input.empty())

    try:
        if getattr(cli, "_wake_word_active", False):
            from tools.wake_word import pause_listening
            if not pause_listening(owner=cli):
                return
            cli._wake_suspended = True
        deadline = time.monotonic() + cfg["playback_timeout_seconds"]
        while not cli._voice_tts_done.wait(timeout=0.1):
            if cancelled() or time.monotonic() >= deadline:
                return
        while (voice_mode.is_audio_output_active()
               or getattr(cli, "_voice_fd_active", threading.Event()).is_set()):
            if cancelled() or time.monotonic() >= deadline:
                return
            cancel.wait(0.05)
        if cancelled():
            return
        from hermes_cli.voice_ready_cue import play_ready_cue
        play_ready_cue(cli)
        if cancelled():
            return
        deadline = time.monotonic() + cfg["timeout_seconds"]
        logger.info("Voice follow-up window opened for session %s", session)
        wav = voice_mode.full_duplex_listen(
            lambda: cancelled() or time.monotonic() >= deadline,
            is_playing=voice_mode.is_audio_output_active,
            calibration_ms=150, max_utterance_ms=30000)
        if not wav or cancelled():
            return
        result = voice_mode.transcribe_recording(wav, model=cli._voice_stt_model())
        transcript = (result.get("transcript") or "").strip() if result.get("success") else ""
        if not transcript or cancelled() or voice_mode.is_tts_echo(transcript, spoken):
            return
        if voice_mode.is_voice_stop_phrase(transcript):
            cli._voice_followup_resume = None
            cli._disable_voice_mode()
            return
        cli._voice_followup_resume = None
        cli._pending_input.put(make_message(transcript))
        logger.info("Voice follow-up submitted in session %s", session)
    except Exception:
        logger.warning("Voice follow-up ended without an answer", exc_info=True)
    finally:
        try:
            if wav:
                Path(wav).unlink(missing_ok=True)
        finally:
            with cli._voice_lock:
                cli._voice_processing = False
        app = getattr(cli, "_app", None)
        if app is not None:
            app.invalidate()
        logger.info("Voice follow-up window closed for session %s", session)
