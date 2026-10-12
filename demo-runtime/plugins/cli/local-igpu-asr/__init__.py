"""Native STT extension: no model prompts, tools or core harness changes."""
import logging
import time
from pathlib import Path

from agent.transcription_provider import TranscriptionProvider
from .transport import MAX_AUDIO_BYTES, request

log = logging.getLogger(__name__)


def settings():
    from tools.transcription_tools import _load_stt_config
    return _load_stt_config().get('openvino_igpu') or {}


class IGPUTranscription(TranscriptionProvider):
    name = 'openvino_igpu'
    display_name = 'Local OpenVINO iGPU (English)'

    def is_available(self):
        # Do not wake/load a model or wait behind inference for a picker probe.
        try:
            return Path(settings()['socket']).is_socket()
        except (KeyError, OSError, TypeError):
            return False

    def transcribe(self, file_path, *, model=None, language=None, **extra):
        started = time.monotonic()
        try:
            if language and language.lower() not in ('en', 'en-us', 'en-gb'):
                raise ValueError('This ASR deployment supports English only')
            cfg = settings()
            with open(file_path, 'rb') as handle:
                audio = handle.read(MAX_AUDIO_BYTES + 1)
            if not audio:
                raise ValueError('Audio file is empty')
            result = request(cfg['socket'], audio, timeout=float(cfg.get('timeout', 30)))
            if result.get('success') and not isinstance(result.get('transcript'), str):
                raise ValueError('ASR returned no transcript field')
            log.info('voice_latency stage=asr_igpu seconds=%.3f success=%s',
                     time.monotonic() - started, result.get('success'))
            return {**result, 'provider': self.name}
        except Exception as exc:
            log.warning('Local iGPU ASR failed: %s', exc)
            return {'success': False, 'transcript': '', 'provider': self.name,
                    'error': 'Local iGPU ASR unavailable: ' + str(exc)}


def register(ctx):
    ctx.register_transcription_provider(IGPUTranscription())
