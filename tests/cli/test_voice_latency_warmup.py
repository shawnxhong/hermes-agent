from unittest.mock import Mock
from types import SimpleNamespace
import sys

from hermes_cli import voice_wake_ack as ack
from hermes_cli import config


def test_only_fixed_configured_ack_is_cached(monkeypatch):
    cfg={'voice':{'tool_ack':{'cache_audio':True,'phrases':{'en':['Sure, let me check.']}}},
         'tts':{'provider':'local'}}
    monkeypatch.setattr(config,'load_config',lambda:cfg)
    cache=Mock();monkeypatch.setattr(ack,'cached_audio',cache)
    assert ack.cached_turn_ack('A regular answer.') is None
    cache.assert_not_called()
    assert ack.cached_turn_ack('Sure, let me check.') == cache.return_value
    cache.assert_called_once_with({'text':'Sure, let me check.','tts':cfg['tts']})
    cfg['voice']['tool_ack']['cache_audio']=False
    assert ack.cached_turn_ack('Sure, let me check.') is None


def test_asr_prewarm_loads_once_without_download(monkeypatch):
    from tools import transcription_tools as stt
    factory=Mock()
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=factory))
    monkeypatch.setattr(stt,'_local_model',None)
    monkeypatch.setattr(stt,'_local_model_name',None)
    monkeypatch.setattr(stt,'_load_stt_config',lambda:{'provider':'local','local':{'prewarm':True,'model':'large-v3-turbo'}})
    stt.prewarm_local_model();stt.prewarm_local_model()
    factory.assert_called_once()
    assert factory.call_args.kwargs['local_files_only'] is True


def test_disabled_prewarm_does_not_load(monkeypatch):
    from tools import transcription_tools as stt
    factory=Mock()
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=factory))
    monkeypatch.setattr(stt,'_load_stt_config',lambda:{'provider':'local','local':{}})
    stt.prewarm_local_model()
    factory.assert_not_called()
