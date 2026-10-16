"""Reference selection and AEC fallback must not prevent ordinary ASR startup."""
import importlib.util
import os
from pathlib import Path
from unittest.mock import Mock
import pytest
import yaml
from tools.wake_audio import resolve_reference, reference_connected, start_capture_with_aec

spec = importlib.util.spec_from_file_location('wake_audio_service', Path(__file__).resolve().parents[2]/'demo-runtime/wake/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def sink(number, name, desc):
    return {'id': number, 'info': {'props': {'media.class': 'Audio/Sink', 'node.name': name, 'node.description': desc}}}


GRAPH = [sink(50, 'hdmi', 'HDMI'), sink(90, 'speaker', 'Speaker'),
         {'id': 40, 'props': {'metadata.name': 'default'}, 'metadata': [
             {'key': 'default.configured.audio.sink', 'value': {'name': 'hdmi'}},
             {'key': 'default.audio.sink', 'value': {'name': 'speaker'}}]}]
DEVICES = [(0, 'HDMI'), (3, 'Speaker')]


@pytest.mark.parametrize('reference', ['default', 'speaker', 'PLAYBACK_DEVICE:3'])
def test_effective_default_or_explicit_endpoint_never_uses_first_sink(reference):
    assert resolve_reference(reference, GRAPH, DEVICES) == ('PLAYBACK_DEVICE:3', 'speaker')


@pytest.mark.parametrize('reference', ['missing', 'PLAYBACK_DEVICE:999', 'PLAYBACK_DEVICE:-1', 'PLAYBACK_DEVICE'])
def test_invalid_endpoint_cannot_silently_select_zero(reference):
    with pytest.raises(ValueError):
        resolve_reference(reference, GRAPH, DEVICES)


def test_duplicate_descriptions_are_not_guessed():
    with pytest.raises(ValueError):
        resolve_reference('speaker', GRAPH + [sink(91, 'other', 'Speaker')], DEVICES)


def test_disabled_mode_does_not_enumerate_or_resolve():
    native, read = Mock(), Mock(side_effect=AssertionError('must not resolve'))
    assert start_capture_with_aec(native, 'mic', {'aec': 'none', 'aec_reference': 'missing'}, Mock(), read_graph=read) == 'none'
    native.start_capture.assert_called_once_with('mic', 'none', '')
    native.playback_devices.assert_not_called()


@pytest.mark.parametrize('failure', ['resolution', 'start', 'verification'])
def test_aec_failure_cleans_up_then_starts_plain_capture(failure):
    native = Mock()
    native.playback_devices.return_value = DEVICES
    native.capture_alive.return_value = False
    native.start_capture.side_effect = [RuntimeError('AEC unavailable'), None] if failure == 'start' else None
    read = Mock(side_effect=RuntimeError('no PipeWire')) if failure == 'resolution' else Mock(return_value=GRAPH)
    event = Mock()
    assert start_capture_with_aec(native, 'mic', {'aec': 'os', 'aec_reference': 'default'}, event, read_graph=read) == 'none'
    calls = [c[0] for c in native.mock_calls]
    assert calls.index('stop_capture') < max(i for i, name in enumerate(calls) if name == 'start_capture')
    native.start_capture.assert_called_with('mic', 'none', '')
    native.stop_capture.assert_called_once()
    assert any(c.kwargs['event'] == 'aec_fallback' for c in event.call_args_list)


def test_plain_failure_is_reported_without_retry_loop():
    native = Mock()
    native.start_capture.side_effect = RuntimeError('microphone unavailable')
    with pytest.raises(RuntimeError, match='microphone unavailable'):
        start_capture_with_aec(native, 'mic', {'aec': 'os', 'aec_reference': 'default'}, Mock(),
                               read_graph=Mock(side_effect=ValueError('missing reference')))
    native.start_capture.assert_called_once_with('mic', 'none', '')


def test_reference_verification_and_success_require_actual_monitor_link():
    nodes = [
        {'id': 100, 'info': {'props': {'node.name': f'aisp-aec-{os.getpid()}-0', 'client.id': 101}}},
        {'id': 102, 'info': {'props': {'target.object': 'speaker', 'client.id': 101}}},
        {'id': 103, 'type': 'PipeWire:Interface:Link', 'info': {'output-node-id': 90, 'input-node-id': 102}}]
    assert reference_connected(GRAPH, GRAPH+nodes, 'speaker')
    assert not reference_connected(GRAPH, GRAPH+nodes[:-1], 'speaker')
    assert not reference_connected(GRAPH, GRAPH+nodes, 'hdmi')
    native = Mock()
    native.playback_devices.return_value = DEVICES
    assert start_capture_with_aec(native, 'mic', {'aec': 'os', 'aec_reference': 'default'}, Mock(),
                                 read_graph=Mock(side_effect=[GRAPH, GRAPH+nodes])) == 'os'
    native.stop_capture.assert_not_called()
    native.start_capture.assert_called_once_with('mic', 'os', 'PLAYBACK_DEVICE:3')


@pytest.mark.parametrize('language', ['auto', 'en', 'zh'])
def test_language_reaches_native_argument(tmp_path, monkeypatch, language):
    config = service.load_config()
    config['asr']['source_language'] = language
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    lib = Mock()
    lib.wake_create.return_value = 1
    monkeypatch.setattr(service.C, 'CDLL', Mock(return_value=lib))
    service.Native('test', 'model', 'GPU', lambda *args: None, config=service.load_config(path))
    assert lib.wake_create.call_args.args[3] == language.encode()


def test_bad_language_is_not_silently_converted(tmp_path):
    config = service.load_config()
    config['asr']['source_language'] = 'invalid'
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='source_language'):
        service.load_config(path)
