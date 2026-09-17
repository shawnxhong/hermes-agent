"""Scene control contract, using real socket and queue boundaries without audio."""
import json
import queue
import socket
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_cli import voice_scenes as scenes


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr('agent.skill_commands.build_preloaded_skills_prompt',
                        lambda names: ('skill:' + names[0], names, []))
    monkeypatch.setattr('tools.voice_mode.stop_playback', Mock())
    monkeypatch.setattr('tools.voice_mode.stop_thinking_sound', Mock())
    cli = SimpleNamespace(
        session_id='old', system_prompt='base\n\nold-skill',
        _preloaded_skills_prompt='old-skill', agent=None,
        _pending_input=queue.Queue(), _interrupt_queue=queue.Queue(),
        _voice_tts_done=threading.Event(), _attached_images=[],
        _reset_stream_state=Mock(), _clear_active_overlays_for_interrupt=Mock(),
        finalize_preloaded_skills=Mock(), _should_exit=False,
    )
    events = []
    def new_session(**kw):
        events.append('reset')
        cli.session_id += '-new'
    cli.new_session = new_session
    audio = {scenes.ACK: scenes.ACK,
             **{v['announcement']: v['announcement'] for v in scenes.DEFAULT_SCENES.values()}}
    def play(text):
        assert scenes.switching(cli)
        events.append(text)
        return True
    controller = scenes.SceneController(cli, scenes.DEFAULT_SCENES, audio, play=play)
    yield cli, controller, events
    controller.close()


def test_ack_reset_ready_and_replace_skill(rig):
    cli, controller, events = rig
    cli._pending_input.put('old transcript')
    controller.request('travel')
    controller.ack_thread.join(2)
    assert events == [scenes.ACK]
    assert controller.apply_pending()
    assert events == [scenes.ACK, 'reset', 'Travel assistant ready.']
    assert cli.system_prompt == 'base\n\nskill:travel-concierge'
    assert cli.preloaded_skills == ['travel-concierge']
    assert cli._pending_input.empty() and not scenes.switching(cli)
    assert cli._wake_suspended and not cli._voice_continuous
    controller.request('home')
    controller.apply_pending()
    assert cli.system_prompt == 'base\n\nskill:demo-home-assistant'
    assert 'travel-concierge' not in cli.system_prompt


def test_switch_revokes_old_generation_and_does_not_scope_im(rig):
    from agent.scene_scope import bind, check_call
    cli, controller, _ = rig
    controller.active = 'home'
    previous = controller.scope()
    cli.agent = Mock(_scene_scope=previous)
    with bind(previous):
        assert check_call('demo_home_set') is None
    controller.request('travel')
    assert previous.revoked.is_set()
    with bind(previous):
        assert check_call('demo_home_set') is not None
    controller.apply_pending()
    current = controller.scope()
    assert current.generation > previous.generation
    assert current.session_id != previous.session_id
    assert current.active_skills == frozenset({'travel-concierge'})
    with bind(current):
        assert check_call('demo_home_status') is not None
        assert check_call('web_search') is None
    assert check_call('demo_home_status') is None


def test_latest_request_wins_and_ack_coalesces(rig):
    cli, controller, events = rig
    entered, release = threading.Event(), threading.Event()
    def play(text):
        events.append(text)
        if text == scenes.ACK:
            entered.set()
            assert release.wait(3)
        return True
    controller.play = play
    controller.request('travel')
    assert entered.wait(2)
    controller.request('home')
    release.set()
    controller.apply_pending()
    assert events.count(scenes.ACK) == 1
    assert 'Travel assistant ready.' not in events
    assert controller.active == 'home'


def test_invalid_skill_keeps_session(rig, monkeypatch):
    cli, controller, events = rig
    monkeypatch.setattr('agent.skill_commands.build_preloaded_skills_prompt',
                        lambda names: ('', [], names))
    with pytest.raises(ValueError):
        controller.request('home')
    assert cli.session_id == 'old' and not events and not scenes.switching(cli)


def test_socket_and_single_owner(rig, monkeypatch):
    cli, controller, events = rig
    folder = tempfile.TemporaryDirectory(prefix='scene-test-')
    monkeypatch.setattr(scenes, 'socket_path', lambda: Path(folder.name) / 'control.sock')
    controller.start()
    assert scenes.socket_path().stat().st_mode & 0o777 == 0o600
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(3)
        conn.connect(str(scenes.socket_path()))
        conn.sendall(b'{"action":"switch","scene":"home"}\n')
        reply = json.loads(conn.makefile().readline())
    assert reply['ok'] and reply['pending'] == 'home'
    other = scenes.SceneController(cli, {}, {})
    try:
        with pytest.raises(BlockingIOError):
            other.start()
    finally:
        other.close()
    assert scenes.socket_path().exists()
    controller.apply_pending()
    assert controller.status()['scene'] == 'home'
    controller.close()
    folder.cleanup()


def test_scene_blocks_capture_wake_and_followup(rig):
    from cli import HermesCLI
    from hermes_cli.voice_followup import start_followup
    cli, controller, _ = rig
    cli._scene_switching = True
    HermesCLI._voice_start_recording(cli)
    HermesCLI._on_wake_word(cli)
    assert not start_followup(cli, 'When?', voice_input=True, make_message=str)


def test_late_tts_does_not_play_in_new_scene(rig):
    from cli import HermesCLI
    cli, _, _ = rig
    cli._scene_generation = 2
    HermesCLI._voice_speak_response(cli, 'Old answer', 1)


def test_failed_reset_does_not_announce_ready(rig):
    cli, controller, events = rig
    cli.new_session = Mock(side_effect=RuntimeError('reset failed'))
    controller.request('home')
    controller.apply_pending()
    assert controller.error == 'reset failed'
    assert 'Home assistant ready.' not in events
    assert not scenes.switching(cli)


def test_repeat_scene_after_debounce_resets_again(rig):
    cli, controller, _ = rig
    controller.request('travel')
    controller.apply_pending()
    session = cli.session_id
    controller.last_press = (None, 0)
    controller.request('travel')
    controller.apply_pending()
    assert session != cli.session_id
    assert cli.system_prompt.count('skill:travel-concierge') == 1


def test_hard_interrupt_is_control_not_user_message(rig):
    cli, controller, _ = rig
    class Agent:
        def hard_interrupt(self):
            self.stopped = True
    cli.agent = Agent()
    controller.request('home')
    assert cli.agent.stopped
    assert cli._interrupt_queue.empty()
    controller.apply_pending()


def test_queued_mail_cancel_does_not_affect_other_session(monkeypatch, tmp_path):
    from hermes_cli import voice_outbox
    from hermes_cli.voice_delivery import TaskStore
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(voice_outbox, 'kick', Mock())
    store = TaskStore()
    tasks = {}
    for session in ('voice', 'im'):
        task = store.start(session, 'A report')
        tasks[session] = store.publish(session, task, body=session, summary='Summary')
        voice_outbox.enqueue(store, session, tasks[session], 'a@example.com')
    voice_outbox.cancel_session('voice')
    with pytest.raises(RuntimeError, match='cancelled'):
        voice_outbox.enqueue(store, 'voice', tasks['voice'], 'b@example.com')
    sender = Mock(return_value={'success': True})
    voice_outbox.drain(sender)
    sender.assert_called_once_with('a@example.com', 'im')
