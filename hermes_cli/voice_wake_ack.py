"""Cached wake acknowledgement before capture, independent of agent-turn TTS."""
import hashlib
import json
import logging
from pathlib import Path
import shutil
import tempfile
import time

logger = logging.getLogger(__name__)


def settings():
    from hermes_cli.config import load_config
    from utils import is_truthy_value
    config = load_config()
    voice = config.get("voice") or {}
    raw = voice.get("wake_ack") if isinstance(voice, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    text = raw.get("text", "Hi, I'm here.")
    if not isinstance(text, str) or not text.strip():
        text = "Hi, I'm here."
    # A wake cue is not a second assistant answer.
    text = " ".join(text.split()[:12])[:80]
    return {"enabled": is_truthy_value(raw.get("enabled"), default=False),
            "text": text, "tts": config.get("tts") or {}}


def cached_audio(config):
    """Generate once via the configured TTS, then publish a complete cache file."""
    from hermes_constants import get_hermes_home
    from tools.tts_tool import text_to_speech_tool
    identity = json.dumps([config["text"], config["tts"]], sort_keys=True, default=str)
    key = hashlib.sha256(identity.encode()).hexdigest()
    folder = get_hermes_home() / "cache" / "wake-ack"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for suffix in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
        path = folder / (key + suffix)
        if path.is_file() and path.stat().st_size > 0:
            return path
    with tempfile.TemporaryDirectory(prefix="generate-", dir=folder) as temporary:
        output = Path(temporary) / "ack.wav"
        result = text_to_speech_tool(text=config["text"], output_path=str(output))
        result = json.loads(result) if isinstance(result, str) else result
        if not isinstance(result, dict) or not result.get("success"):
            raise RuntimeError("Wake acknowledgement synthesis failed")
        generated = Path(result.get("file_path") or output)
        if not generated.is_file() or generated.stat().st_size <= 0:
            raise RuntimeError("Wake acknowledgement has no audio")
        if generated.suffix not in {".wav", ".mp3", ".ogg", ".flac", ".m4a"}:
            raise RuntimeError("Unsupported wake acknowledgement audio format")
        # Copy inside the same filesystem and rename only complete output.
        staged = Path(temporary) / ("ready" + generated.suffix)
        shutil.copyfile(generated, staged)
        destination = folder / (key + generated.suffix)
        staged.replace(destination)
        return destination


def start_wake_capture(cli):
    """Called only after the wake detector is paused and the session selected."""
    from tools import voice_mode
    session = cli.session_id
    with cli._voice_lock:
        if cli._voice_recording or cli._voice_processing or cli._agent_running:
            return
        # Prevent the wake watchdog from resuming during generation/playback.
        cli._voice_processing = True

    def cancelled():
        return (getattr(cli, "_should_exit", False) or not cli._voice_mode
                or not cli._wake_word_active or cli.session_id != session
                or cli._agent_running or not cli._pending_input.empty())

    try:
        config = settings()
        if config["enabled"] and cli._voice_tts and not cancelled():
            cli._voice_tts_done.clear()
            try:
                path = cached_audio(config)
                if cancelled():
                    return
                cli._voice_last_tts_text = config["text"]
                if not voice_mode.play_audio_file(str(path)):
                    raise RuntimeError("Wake acknowledgement playback failed")
                # Allow the short speaker tail to settle before microphone open.
                time.sleep(0.15)
                logger.info("Wake acknowledgement played before capture")
            except Exception:
                # Keep the existing recording beep/capture usable on TTS failure.
                logger.warning("Wake acknowledgement unavailable; continuing to capture", exc_info=True)
            finally:
                cli._voice_tts_done.set()
        if not cancelled():
            cli._voice_start_recording()
    finally:
        with cli._voice_lock:
            cli._voice_processing = False
