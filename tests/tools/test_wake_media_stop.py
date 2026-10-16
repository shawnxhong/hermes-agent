"""Local stop commands bypass Hermes while capture/EOS remain available."""
import importlib.util
import json
import logging
from pathlib import Path
import socket
import threading
from unittest.mock import Mock

import pytest

from tools.media_stop_policy import is_media_stop_command


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wake_stop_service', ROOT / 'demo-runtime/wake/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
EOS = 2


def test_hotwords_are_valid_commands_and_keep_custom_bias():
    context = service.context_with_hotwords('custom-domain')
    assert context.startswith('custom-domain\n')
    assert all(is_media_stop_command(phrase) and phrase in context for phrase in service.load_config()["asr"]["hotwords"] if phrase.startswith(("stop ", "close ", "turn off ")))
    assert service.context_with_hotwords(context) == context


def test_config_controls_context_and_matching_without_hidden_defaults(tmp_path, monkeypatch):
    path = tmp_path / 'config.yaml'
    path.write_text('asr:\n  context: custom-domain\n  hotwords: [custom wake]\nwake:\n  aliases: [custom wake]\n')
    config = service.load_config(path)
    lib = Mock()
    lib.wake_create.return_value = 1
    monkeypatch.setattr(service.C, 'CDLL', Mock(return_value=lib))
    service.Native('/test/libwake.so', '/models/qwen3-asr', 'GPU', lambda *a: None, config=config)
    assert lib.wake_create.call_args.args[2] == b'custom-domain\ncustom wake'
    server = service.Server(logging.getLogger('config-wake'), config['wake']['aliases'], EOS)
    assert server.gate.match(1, 'Hello Intel')[0] == 'miss'
    assert server.gate.match(1, 'custom wake')[0] == 'trigger'
    # Empty hints explicitly disable recognition bias, without fallback literals.
    config['asr'] = {'context': '', 'hotwords': []}
    assert service.context_with_hotwords(config=config) == ''


@pytest.mark.parametrize('content', [
    '', 'asr: {}',
    'asr: {context: "", hotwords: hello}\nwake: {aliases: [hello]}',
    'asr: {context: "", hotwords: [42]}\nwake: {aliases: [hello]}',
    'asr: {context: "", hotwords: []}\nwake: {aliases: []}',
    'asr: {context: "", hotwords: []}\nwake: {aliases: [hello], typo: true}',
])
def test_invalid_config_fails_before_native_start(tmp_path, content):
    path = tmp_path / 'config.yaml'
    path.write_text(content)
    with pytest.raises(ValueError):
        service.load_config(path)


@pytest.mark.parametrize('context', ['', 'Hello Intel'])
def test_native_passes_media_hotwords_to_actual_bridge_argument(monkeypatch, context):
    lib = Mock()
    lib.wake_create.return_value = 1
    monkeypatch.setattr(service.C, 'CDLL', Mock(return_value=lib))
    native = service.Native('/test/libwake.so', '/models/qwen3-asr', 'GPU', lambda *args: None, context)
    passed = lib.wake_create.call_args.args[2].decode()
    assert native.handle == 1
    assert 'stop playback' in passed and 'stop music' in passed and 'stop video' in passed
    assert all(phrase in passed for phrase in ('Hello Intel', 'Hi Intel', 'Hey Intel', 'Intel Intel'))
    assert 'thank you' not in passed.lower()
    if context:
        assert passed.startswith(context+'\n')


@pytest.fixture
def make_server():
    servers = []

    def make(request=None):
        if request is None:
            request = Mock(return_value={'success': True, 'action': 'stopped', 'state': 'inactive'})
        server = service.Server(logging.getLogger('test-wake-stop'), ['Hello Intel'], EOS, request)
        servers.append(server)
        return server, request

    yield make
    for server in servers:
        server.close()


def drain(server):
    # Tests use bounded socket/mock operations; unfinished tasks must reach zero.
    done = threading.Event()
    thread = threading.Thread(target=lambda: (server.media_stop.pending.join(), done.set()), daemon=True)
    thread.start()
    assert done.wait(3), 'media worker did not drain'


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


@pytest.mark.parametrize('mode', [None, 'wake', 'recording', 'delivered'])
def test_partial_stops_without_eos_or_wake_and_never_sends_hermes_event(make_server, mode):
    server, request = make_server()
    conn = Mock()
    if mode:
        server.arm(conn, 'recording' if mode == 'recording' else 'wake', 'rec', 100)
        server.delivered = mode == 'delivered'
    server.result(99, 0, b'Stop music')  # Local control also works before arm boundary.
    server.result(99, 0, b'Stop playback')
    server.result(99, EOS, b'')
    server.result(99, EOS, b'Hello Intel')  # Even later revisions stay consumed.
    drain(server)
    request.assert_called_once_with('stop')
    conn.sendall.assert_not_called()
    assert 99 not in server.recording_speech


def test_new_utterance_stops_again_and_normal_wake_still_delivers(make_server):
    server, request = make_server()
    conn = Mock()
    server.arm(conn, 'wake', None, 100)
    for ts in (101, 102):
        server.result(ts, 0, b'stop playback')
    server.result(103, 0, b'Hello Intel')
    drain(server)
    assert request.call_count == 2
    assert json.loads(conn.sendall.call_args.args[0])['event'] == 'wake'


def test_stopping_does_not_end_recording_but_next_question_eos_does(make_server):
    server, _ = make_server()
    conn = Mock()
    server.arm(conn, 'recording', 'rec', 100)
    server.result(101, 0, b'stop')
    server.result(101, 0, b'stop video')
    server.result(101, EOS, b'')
    conn.sendall.assert_not_called()
    server.result(102, 0, b'What time is it?')
    server.result(102, EOS, b'')
    assert json.loads(conn.sendall.call_args.args[0])['event'] == 'end_recording'
    drain(server)


@pytest.mark.parametrize('failure', [OSError('missing bridge'), TimeoutError('slow bridge'),
    ValueError('bad JSON'), {'success': False}, {}, None])
def test_failure_is_logged_once_and_does_not_poison_next_command(make_server, failure):
    request = Mock(side_effect=[failure, {'success': True, 'action': 'stopped', 'state': 'inactive'}])
    server, _ = make_server(request)
    server.event = Mock()
    server.media_stop.event = server.event
    server.result(101, 0, b'stop music')
    server.result(101, EOS, b'stop music')
    server.result(102, 0, b'stop music')
    drain(server)
    assert request.call_count == 2
    assert any(c.kwargs['event'] == 'media_stop_failed' for c in server.event.call_args_list)
    assert any(c.kwargs['event'] == 'media_stop_completed' for c in server.event.call_args_list)


def test_blocked_ipc_and_full_queue_do_not_block_asr_or_hold_server_lock(make_server):
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()

    def slow_stop(action):
        entered.set()
        assert release.wait(3)
        return {'success': True, 'action': 'stopped', 'state': 'inactive'}

    server, _ = make_server(slow_stop)
    conn = Mock()
    server.arm(conn, 'wake', None, 100)
    server.event = Mock()
    server.media_stop.event = server.event
    server.result(101, 0, b'stop music')
    assert entered.wait(1)

    def callbacks():
        for ts in range(102, 136):
            server.result(ts, 0, b'stop music')
        server.result(200, 0, b'Hello Intel')
        returned.set()

    thread = threading.Thread(target=callbacks)
    thread.start()
    try:
        assert returned.wait(1), 'ASR callback blocked behind host media IPC'
        conn.sendall.assert_called_once()
        assert any(c.kwargs.get('reason') == 'queue_full' for c in server.event.call_args_list)
    finally:
        release.set()
        thread.join(2)
    drain(server)


def test_dedup_history_is_bounded(make_server):
    server, _ = make_server()
    server.media_stop.submit = Mock()
    for ts in range(1100):
        server.result(ts, 0, b'stop music')
    assert len(server.stop_sentences) == 1024
    assert 0 not in server.stop_sentences and 1099 in server.stop_sentences


def test_real_unix_rpc_from_service_without_agent_imports(make_server, tmp_path, monkeypatch):
    path = tmp_path / 'service.sock'
    monkeypatch.setenv('HERMES_MEDIA_BRIDGE', str(path))
    (tmp_path / 'epoch').write_text('test-epoch')
    received = []
    errors = []
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        listener.listen()
        listener.settimeout(3)

        def bridge():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(3)
                    received.append(json.loads(conn.makefile('rb').readline()))
                    conn.sendall(b'{"success":true,"action":"stopped","state":"inactive"}\n')
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=bridge)
        worker.start()
        server, _ = make_server(service.playback_request)
        server.result(101, 0, b'stop music')
        drain(server)
        worker.join(3)
        assert not worker.is_alive() and not errors
    assert received == [{'action': 'stop', 'target': '', 'epoch': 'test-epoch'}]
