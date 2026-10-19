"""Wake configuration and ordinary EOS without a device-wide stop shortcut."""
import importlib.util
import json
import logging

import yaml
from pathlib import Path
import socket
from unittest.mock import Mock

import pytest

from tools.media_stop_policy import is_media_stop_command


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wake_stop_service', ROOT / 'demo-runtime/wake/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
EOS = 2


def test_packaged_default_enables_os_aec():
    assert service.load_config()['audio']['aec'] == 'os'

@pytest.mark.parametrize('level,expected', [('low', 0), ('medium', 1), ('high', 2), (None, 1)])
def test_split_level_reaches_native(tmp_path, monkeypatch, level, expected):
    config = service.load_config()
    config['vad'].pop('split_sensitivity', None)
    if level is not None:
        config['vad']['split_sensitivity'] = level
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    parsed = service.load_config(path)
    lib = Mock()
    lib.wake_create_with_split.return_value = 1
    monkeypatch.setattr(service.C, 'CDLL', Mock(return_value=lib))
    native = service.Native('test', 'model', 'GPU', lambda *a: None, config=parsed)
    assert lib.wake_create_with_split.call_args.args[6] == expected
    assert lib.wake_create_with_split.argtypes[6] is service.C.c_int32
    assert lib.wake_create_with_split.call_args.args[7] is native.cb


@pytest.mark.parametrize('bad', [None, True, 0, 1, 'LOW', 'mid', '', [], {}])
def test_invalid_split_level(tmp_path, bad):
    config = service.load_config()
    config['vad']['split_sensitivity'] = bad
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='split_sensitivity'):
        service.load_config(path)




def test_hotwords_keep_line_separated_sdk_format():
    context = service.asr_context()
    assert context.splitlines() == service.load_config()['asr']['hotwords']


def test_config_controls_context_and_matching_without_hidden_defaults(tmp_path, monkeypatch):
    path = tmp_path / 'config.yaml'
    custom = service.load_config()
    custom['asr']['hotwords'] = ['custom wake']
    custom['wake']['aliases'] = ['custom wake']
    path.write_text(yaml.safe_dump(custom))
    config = service.load_config(path)
    lib = Mock()
    lib.wake_create_with_split.return_value = 1
    monkeypatch.setattr(service.C, 'CDLL', Mock(return_value=lib))
    service.Native('/test/libwake.so', '/models/qwen3-asr', 'GPU', lambda *a: None, config=config)
    assert lib.wake_create_with_split.call_args.args[2] == b'custom wake'
    server = service.Server(logging.getLogger('config-wake'), config['wake']['aliases'], EOS)
    assert server.gate.match(1, 'Hello Intel')[0] == 'miss'
    assert server.gate.match(1, 'custom wake')[0] == 'trigger'
    # Empty hints explicitly disable recognition bias, without fallback literals.
    config['asr'] = {'hotwords': []}
    assert service.asr_context(config) == ''


@pytest.mark.parametrize('content', [
    '', 'asr: {}',
    'asr: {context: extra, hotwords: []}\nwake: {aliases: [hello]}',
    'asr: {hotwords: hello}\nwake: {aliases: [hello]}',
    'asr: {hotwords: [42]}\nwake: {aliases: [hello]}',
    'asr: {hotwords: []}\nwake: {aliases: []}',
    'asr: {hotwords: []}\nwake: {aliases: [hello], typo: true}',
])
def test_invalid_config_fails_before_native_start(tmp_path, content):
    path = tmp_path / 'config.yaml'
    parsed = yaml.safe_load(content)
    if isinstance(parsed, dict):
        parsed['vad'] = service.load_config()['vad']
        parsed['recording_quiet'] = service.load_config()['recording_quiet']
        parsed['audio'] = service.load_config()['audio']
        if isinstance(parsed.get('asr'), dict):
            parsed['asr']['source_language'] = 'auto'
        content = yaml.safe_dump(parsed)
    path.write_text(content)
    with pytest.raises(ValueError):
        service.load_config(path)




@pytest.mark.parametrize('phrase', ['stop playback', 'stop music', 'Please close the video.'])
def test_stop_phrase_is_ordinary_recording_text_and_never_calls_media(tmp_path, monkeypatch, phrase):
    # A real listening socket must receive no connection from the wake service.
    listener = socket.socket(socket.AF_UNIX)
    path = tmp_path / 'media.sock'
    listener.bind(str(path))
    listener.listen()
    listener.settimeout(.05)
    monkeypatch.setenv('HERMES_MEDIA_BRIDGE', str(path))
    server = service.Server(logging.getLogger('no-shortcut'), ['Hello Intel'], EOS)
    conn = Mock()
    try:
        server.arm(conn, 'wake', None, 0)
        server.result(10, 0, phrase.encode())
        server.result(10, EOS, b'')
        conn.sendall.assert_not_called()
        server.arm(conn, 'recording', 'test', 0)
        server.result(11, 0, phrase.encode())
        server.result(11, EOS, b'')
        conn.sendall.assert_called_once()
        assert json.loads(conn.sendall.call_args.args[0])['event'] == 'end_recording'
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()


@pytest.mark.parametrize('key', ['threshold', 'block_rms_threshold'])
@pytest.mark.parametrize('value', [True, '0.35', -.1, 1.1, float('nan'), float('inf')])
def test_invalid_vad_rejected(tmp_path, key, value):
    config = service.load_config()
    config['vad'][key] = value
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='vad.'):
        service.load_config(path)


@pytest.mark.parametrize('threshold,rms', [(0, 1), (.6, .4), (1, 0)])
def test_vad_yaml_reaches_native_init(tmp_path, monkeypatch, threshold, rms):
    config = service.load_config()
    config['vad'] = {'threshold': threshold, 'block_rms_threshold': rms}
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    lib = Mock()
    lib.wake_create_with_split.return_value = 1
    monkeypatch.setattr(service.C, 'CDLL', Mock(return_value=lib))
    service.Native('test', 'model', 'GPU', lambda *args: None, service.load_config(path))
    assert lib.wake_create_with_split.call_args.args[4:6] == (threshold, rms)

@pytest.mark.parametrize('text', [
    'stop playback', 'STOP MUSIC!', ' Stop video. ', 'Please close the video.',
    'Could you stop the music please?', 'stop playing the song', 'turn off all audio now',
])
def test_shared_matcher_and_feishu_use_identical_full_string_rules(text):
    spec = importlib.util.spec_from_file_location('stop_policy_check',
        ROOT / 'demo-runtime/plugins/gateway/demo-media/__init__.py')
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    assert is_media_stop_command(text)
    request = {'messages': [{'role': 'user', 'content': text}],
               'tools': [{'function': plugin.SCHEMA}]}
    assert plugin.require_stop_tool(request=request, platform='feishu', session_id='s', turn_id='t')


@pytest.mark.parametrize('text', [
    'Do not stop music.', 'What does stop music mean?', 'Say "stop music".',
    'Stop music and email a report.', 'stop playback, please', 'Stop',
    'Hello Intel stop music', '停止播放', 'Stop the music lesson.', '', None,
])
def test_unrelated_or_extended_forms_do_not_match(text):
    assert not is_media_stop_command(text)
