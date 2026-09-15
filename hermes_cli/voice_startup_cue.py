"""Optional local startup audio, called only while the wake listener is paused."""
from pathlib import Path


def play_startup_cue():
    import threading
    def warm():
        import logging
        try:
            from tools.transcription_tools import prewarm_local_model
            prewarm_local_model()
            from hermes_cli.voice_wake_ack import cached_turn_ack
            from hermes_cli.config import load_config
            phrases = ((load_config().get('voice') or {}).get('tool_ack') or {}).get('phrases') or {}
            for values in phrases.values():
                for phrase in (values if isinstance(values, list) else [values]):
                    cached_turn_ack(phrase)
        except Exception:
            logging.getLogger(__name__).warning('Voice warmup failed', exc_info=True)
    threading.Thread(target=warm, name='voice-prewarm', daemon=True).start()
    from hermes_cli.config import load_config
    from tools.voice_mode import play_audio_file, play_beep

    voice = load_config().get("voice") or {}
    path = voice.get("startup_cue_file") if isinstance(voice, dict) else None
    if not path:
        play_beep(frequency=1040, count=2)
        return
    audio = Path(path).expanduser()
    if not audio.is_file() or not play_audio_file(str(audio)):
        raise RuntimeError("Configured startup cue could not be played")
