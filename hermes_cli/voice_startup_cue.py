"""Optional local startup audio, called only while the wake listener is paused."""
from pathlib import Path


def play_startup_cue():
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
