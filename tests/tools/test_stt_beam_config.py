import pytest

from tools.transcription_tools import build_local_transcribe_kwargs


@pytest.mark.parametrize('beam', [1, 3, 5, 10])
def test_explicit_beam_keeps_speech_safeguards(beam):
    kwargs = build_local_transcribe_kwargs({'local': {'beam_size': beam}})
    assert kwargs['beam_size'] == beam
    assert kwargs['condition_on_previous_text'] is False
    assert kwargs['vad_filter'] is True


@pytest.mark.parametrize('beam', [None, True, False, 0, -1, 11, 1.5, '1', {}])
def test_invalid_beam_falls_back_to_existing_default(beam):
    assert build_local_transcribe_kwargs({'local': {'beam_size': beam}})['beam_size'] == 5


def test_missing_beam_preserves_default():
    assert build_local_transcribe_kwargs({})['beam_size'] == 5
